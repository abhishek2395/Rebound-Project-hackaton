"""
Rebound FastAPI Production Gateway
Exposes webhooks for disruption ingestion and Twilio SMS approvals,
enforces sub-100ms async acknowledgements, and provides APIs for the visualizer.
"""

import glob
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.loop import ReboundAgent
from agent.models import CabinClass, DisruptionEvent, FlightOffer, TravelerProfile
from app.db import Database
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio
from clients.twilio_sms import strip_channel_prefix

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rebound.api")

app = FastAPI(
    title="Rebound Agent API",
    description="Autonomous travel disruption recovery agent across Duffel, Google Calendar, Gmail, and Twilio.",
    version="1.0.0",
)

# Enable CORS for local/demo visualizer access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = Database("rebound.db")


# ---------------------------------------------------------------------------
# Client selection
#
# Each integration uses its live client when that integration's credentials are
# present, and its recording fake otherwise. CI and the eval suite ship no
# credentials, so they stay fully offline and deterministic; a populated local
# .env drives genuine Duffel / Twilio / Google calls through the same code.
# ---------------------------------------------------------------------------

def twilio_is_live() -> bool:
    """True when Twilio credentials are complete enough to send a real SMS."""
    return bool(
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_FROM_NUMBER")
    )


def select_clients(
    duffel_fallback: Optional[Any] = None,
    calendar_fallback: Optional[Any] = None,
    gmail_fallback: Optional[Any] = None,
    twilio_fallback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Returns live clients where credentials allow, falling back to the given fakes."""
    if os.getenv("DUFFEL_API_KEY"):
        from clients.duffel import DuffelClient
        duffel = DuffelClient()
    else:
        duffel = duffel_fallback or FakeDuffel()

    if os.path.exists(os.getenv("GOOGLE_TOKEN_PATH", "token.json")):
        from clients.gcal import GoogleCalendarClient
        from clients.gmail import GmailClient
        calendar = GoogleCalendarClient()
        gmail = GmailClient()
    else:
        calendar = calendar_fallback or FakeCalendar()
        gmail = gmail_fallback or FakeGmail()

    if twilio_is_live():
        from clients.twilio_sms import TwilioClient
        twilio = TwilioClient()
    else:
        twilio = twilio_fallback or FakeTwilio()

    logger.info(
        "Client modes -> Duffel: %s | Calendar: %s | Gmail: %s | Twilio: %s",
        type(duffel).__name__,
        type(calendar).__name__,
        type(gmail).__name__,
        type(twilio).__name__,
    )
    return {"duffel": duffel, "calendar": calendar, "gmail": gmail, "twilio": twilio}


def apply_phone_overrides(profile: TravelerProfile) -> TravelerProfile:
    """Points SMS and email at real, verified endpoints without editing committed profiles."""
    traveler_phone = os.getenv("TRAVELER_PHONE")
    if traveler_phone:
        profile.phone = traveler_phone
    traveler_email = os.getenv("TRAVELER_EMAIL")
    if traveler_email:
        profile.email = traveler_email
    pickup_phone = os.getenv("PICKUP_PHONE")
    if pickup_phone and profile.contacts:
        profile.contacts[0].phone = pickup_phone
    return profile


def _run_agent_background(event: DisruptionEvent, run_id: str) -> None:
    """Background worker executing the agent graph outside the webhook request cycle."""
    try:
        clients = select_clients()
        agent = ReboundAgent(db=db, run_id=run_id, **clients)
        agent.profile = apply_phone_overrides(agent.profile)
        record = agent.run(event)
        logger.info("Background run %s completed: action=%s", run_id, record.action.value)
    except Exception as e:
        logger.error("Background run %s failed: %s", run_id, e, exc_info=True)


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "healthy", "service": "rebound-agent", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/webhook/disruption")
async def receive_disruption_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Ingests external flight disruption events.
    Responds with HTTP 200 in <50ms to satisfy strict webhook timeouts and avoid retries.
    """
    try:
        payload = await request.json()
        event = DisruptionEvent.model_validate(payload)
    except Exception as e:
        logger.warning("Malformed disruption webhook payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid payload schema: {str(e)}")

    # Step 1: Idempotency Check
    if db.is_event_processed(event.event_id):
        logger.info("Ignoring duplicate webhook for event_id: %s", event.event_id)
        return {
            "status": "skipped_duplicate",
            "event_id": event.event_id,
            "message": "Event has already been processed.",
        }

    run_id = f"run_{event.event_id}_{int(datetime.now(timezone.utc).timestamp())}"
    logger.info("Accepted disruption event %s (%s). Enqueuing background run %s.", event.event_id, event.type.value, run_id)

    # Queue execution asynchronously
    background_tasks.add_task(_run_agent_background, event, run_id)

    return {
        "status": "accepted",
        "event_id": event.event_id,
        "run_id": run_id,
        "message": "Disruption event queued for processing.",
    }


@app.post("/sms")
async def receive_twilio_sms_webhook(
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
) -> Response:
    """
    Receives inbound SMS replies from travelers via Twilio webhook (form-encoded).
    Resumes the pending approval workflow.
    """
    logger.info("Inbound reply raw From=%r Body=%r", From, Body)
    clean_body = Body.strip().upper()

    # WhatsApp delivers the sender as 'whatsapp:+1555...'; approvals are stored
    # against the bare E.164 number, so normalize before looking one up.
    sender = strip_channel_prefix(From.strip())
    logger.info("Inbound reply sender after strip=%r body_clean=%r", sender, clean_body)

    pending = db.get_pending_approval_by_phone(sender)
    if not pending:
        # Viewer simulator sends the profile-default phone (e.g. +15550000001);
        # env override moves the DB phone to something like TRAVELER_PHONE.
        # As a simulator fallback (only when sender looks like the
        # profile-default range or a localhost call), pick the most recent
        # pending. Real inbound travelers still get the strict-phone lookup.
        if sender.startswith("+1555") or sender.startswith("+15550"):
            pending = db.get_most_recent_pending()
            if pending:
                logger.info("Simulator fallback matched most-recent pending %s", pending.get("event_id"))
    if not pending:
        logger.warning("No pending approval found for phone number %s", sender)
        twiml = "<Response><Message>[Rebound] No active pending rebooking approval found for this number.</Message></Response>"
        return Response(content=twiml, media_type="application/xml")

    event_id = pending["event_id"]
    run_id = pending["run_id"]

    def _resume_background(ev_id: str, reply: str, r_id: str) -> None:
        try:
            agent = ReboundAgent(db=db, run_id=r_id, **select_clients())
            agent.profile = apply_phone_overrides(agent.profile)
            record = agent.resume_with_sms_reply(event_id=ev_id, reply=reply)
            logger.info("Resumed run %s from SMS '%s': action=%s", r_id, reply, record.action.value)
        except Exception as e:
            logger.error("Resume of run %s failed: %s", r_id, e, exc_info=True)

    background_tasks.add_task(_resume_background, event_id, clean_body, run_id)

    twiml = f"<Response><Message>[Rebound] Processing your response: '{clean_body}'. Updates will follow shortly.</Message></Response>"
    return Response(content=twiml, media_type="application/xml")


@app.get("/api/runs")
def list_runs(limit: int = 20) -> Dict[str, Any]:
    """Returns recent runs for the visualizer dashboard."""
    runs = db.get_recent_runs(limit=limit)
    return {"runs": runs}


@app.get("/api/traces/{run_id}")
def get_trace(run_id: str) -> Dict[str, Any]:
    """Retrieves the JSONL trace log for a given run."""
    trace_file = os.path.join("traces", f"{run_id}.jsonl")
    if not os.path.exists(trace_file):
        raise HTTPException(status_code=404, detail="Trace file not found.")

    entries = []
    with open(trace_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line.strip()))

    return {"run_id": run_id, "entries": entries}


@app.post("/api/demo/trigger")
async def trigger_demo_event(
    scenario: str = "S01",
    event_type: str = "cancelled",
) -> Dict[str, Any]:
    """Convenience endpoint to fire demonstration disruption events from fixtures or defaults."""
    matching_fixtures = sorted(glob.glob(f"evals/fixtures/{scenario}*.json"))
    if matching_fixtures:
        with open(matching_fixtures[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        event_data = dict(data["event"])
        if "S10" not in scenario:
            event_data["event_id"] = f"evt_demo_{scenario.lower()}_{int(datetime.now(timezone.utc).timestamp())}"

        # DEMO_ORIGINAL_ORDER_ID lets a live demo point at a real Duffel
        # order that scripts/seed_demo_state.py booked. When set, we override
        # BOTH the order id (so cancel-old targets the real order) AND the
        # flight route (so the agent searches the same route the traveler
        # actually holds a ticket for). The fixture then contributes only the
        # scenario shape — over-threshold, timeout, etc — not the geography.
        seeded_order_id = os.getenv("DEMO_ORIGINAL_ORDER_ID")
        if seeded_order_id:
            event_data["order_id"] = seeded_order_id
            try:
                from clients.duffel import DuffelClient
                dclient = DuffelClient()
                seeded_order = dclient.get_order(seeded_order_id)
                seeded_slice = seeded_order["slices"][0]
                seeded_segment = seeded_slice["segments"][0]
                seeded_last_segment = seeded_slice["segments"][-1]
                event_data["flight"] = dict(event_data.get("flight", {}))
                event_data["flight"]["origin"] = seeded_slice["origin"]["iata_code"]
                event_data["flight"]["destination"] = seeded_slice["destination"]["iata_code"]
                event_data["flight"]["carrier"] = seeded_segment["marketing_carrier"]["iata_code"]
                event_data["flight"]["number"] = seeded_segment["marketing_carrier_flight_number"]
                event_data["flight"]["scheduled_departure"] = seeded_segment["departing_at"]
                event_data["flight"]["scheduled_arrival"] = seeded_last_segment["arriving_at"]
                logger.info(
                    "Demo trigger: substituted seeded route %s->%s from order %s",
                    event_data["flight"]["origin"],
                    event_data["flight"]["destination"],
                    seeded_order_id,
                )
            except Exception as e:
                logger.warning("Could not fetch seeded order %s to override route: %s", seeded_order_id, e)

        event = DisruptionEvent.model_validate(event_data)

        # DEMO_ORIGINAL_TOTAL — the real price of the seeded original ticket,
        # so cost delta is computed against what was actually paid rather than
        # the fixture's synthetic baseline.
        original_order_total = float(os.getenv("DEMO_ORIGINAL_TOTAL", "380.0"))

        # DEMO_APPROVAL_THRESHOLD — for the ask-flow demo shot, force the
        # profile's approval threshold low enough that live Duffel prices
        # will exceed it. Skipped when unset (uses profile's threshold).
        approval_override = os.getenv("DEMO_APPROVAL_THRESHOLD")
        profile = TravelerProfile.model_validate(data["profile"]) if "profile" in data else None

        mock_duffel_data = data.get("mock_duffel", {})
        offers = []
        for item in mock_duffel_data.get("offers", []):
            offers.append(
                FlightOffer(
                    id=item["id"],
                    carrier=item.get("carrier", "ZZ"),
                    flight_number=item.get("flight_number", "ZZ201"),
                    departs_at=datetime.fromisoformat(item["departs_at"]),
                    arrives_at=datetime.fromisoformat(item["arrives_at"]),
                    total_amount=float(item["total_amount"]),
                    segments=int(item.get("segments", 1)),
                    cabin_class=CabinClass(item.get("cabin_class", "economy")),
                )
            )

        failures = mock_duffel_data.get("failures", [])
        cal_data = data.get("calendar", {})
        cal_deadline = datetime.fromisoformat(cal_data.get("deadline", "2026-09-15T09:00:00Z"))

        # Fixture-driven fakes are the fallback; live clients take over wherever
        # credentials exist. For a live demo that means deterministic flight
        # options from the fixture driving a real SMS to a real handset.
        clients = select_clients(
            duffel_fallback=FakeDuffel(offers=offers, failures=failures),
            calendar_fallback=FakeCalendar(
                deadline=cal_deadline,
                injected_failure=cal_data.get("injected_failure"),
            ),
            gmail_fallback=FakeGmail(injected_failure=data.get("mock_gmail", {}).get("injected_failure")),
            twilio_fallback=FakeTwilio(injected_failure=data.get("mock_twilio", {}).get("injected_failure")),
        )

        if profile:
            profile = apply_phone_overrides(profile)
            if approval_override:
                # Force the ask path to fire regardless of live Duffel pricing
                # (real Duffel offers can be cheaper than the fixture assumed).
                profile.mandate.approval_threshold_usd = float(approval_override)

        agent = ReboundAgent(
            db=db,
            profile=profile,
            original_order_total=original_order_total,
            **clients,
        )

        # A fixture's canned reply stands in for a human only while Twilio is
        # faked. With live Twilio the agent must pause in ASK state and wait for
        # the traveler's real inbound SMS to reach /sms.
        sms_reply = None if twilio_is_live() else data.get("sms_reply")
        record = agent.run(event, sms_reply=sms_reply)
        return {
            "record": record.model_dump(),
            "run_id": agent.run_id,
            "scenario": scenario,
            "description": data.get("description", ""),
            "awaiting_sms_reply": twilio_is_live() and record.action.value == "ask",
        }

    # Fallback default (only reached when no fixture matches the scenario)
    event_id = f"evt_demo_{scenario.lower()}_{int(datetime.now(timezone.utc).timestamp())}"
    sample_event = DisruptionEvent(
        event_id=event_id,
        type=event_type,
        flight={
            "carrier": "ZZ",
            "number": "ZZ123",
            "origin": "LHR",
            "destination": "JFK",
            "scheduled_departure": datetime.now(timezone.utc) + timedelta(hours=2),
            "scheduled_arrival": datetime.now(timezone.utc) + timedelta(hours=10),
        },
    )
    agent = ReboundAgent(db=db)
    record = agent.run(sample_event)
    return {"record": record.model_dump(), "run_id": agent.run_id, "scenario": scenario}


if os.path.exists("viewer"):
    app.mount("/viewer", StaticFiles(directory="viewer", html=True), name="viewer")


@app.get("/")
def root_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/viewer/")


