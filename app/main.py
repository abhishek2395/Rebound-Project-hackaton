"""
Rebound FastAPI Production Gateway
Exposes webhooks for disruption ingestion and Twilio SMS approvals,
enforces sub-100ms async acknowledgements, and provides APIs for the visualizer.
"""

import glob
import json
import logging
import os
from datetime import datetime, timezone
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.loop import ReboundAgent
from agent.models import DisruptionEvent
from agent.models import CabinClass, DisruptionEvent, FlightOffer, TravelerProfile
from app.db import Database
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio

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


def _run_agent_background(event: DisruptionEvent, run_id: str) -> None:
    """Background worker executing the agent graph outside the webhook request cycle."""
    try:
        agent = ReboundAgent(db=db, run_id=run_id)
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
    logger.info("Inbound SMS from %s: '%s'", From, Body)
    clean_body = Body.strip().upper()

    pending = db.get_pending_approval_by_phone(From)
    if not pending:
        logger.warning("No pending approval found for phone number %s", From)
        twiml = "<Response><Message>[Rebound] No active pending rebooking approval found for this number.</Message></Response>"
        return Response(content=twiml, media_type="application/xml")

    event_id = pending["event_id"]
    run_id = pending["run_id"]

    def _resume_background(ev_id: str, reply: str, r_id: str) -> None:
        agent = ReboundAgent(db=db, run_id=r_id)
        agent.resume_with_sms_reply(event_id=ev_id, reply=reply)

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

        event = DisruptionEvent.model_validate(event_data)
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
        duf = FakeDuffel(offers=offers, failures=failures)

        cal_data = data.get("calendar", {})
        cal_deadline = datetime.fromisoformat(cal_data.get("deadline", "2026-09-15T09:00:00Z"))
        cal = FakeCalendar(
            deadline=cal_deadline,
            injected_failure=cal_data.get("injected_failure"),
        )
        gml = FakeGmail(injected_failure=data.get("mock_gmail", {}).get("injected_failure"))
        twi = FakeTwilio(injected_failure=data.get("mock_twilio", {}).get("injected_failure"))

        agent = ReboundAgent(
            db=db,
            duffel=duf,
            calendar=cal,
            twilio=twi,
            gmail=gml,
            profile=profile,
            original_order_total=380.0,
        )

        sms_reply = data.get("sms_reply")
        record = agent.run(event, sms_reply=sms_reply)
        return {
            "record": record.model_dump(),
            "run_id": agent.run_id,
            "scenario": scenario,
            "description": data.get("description", ""),
        }

    # Fallback default
    event_id = f"evt_demo_{scenario.lower()}_{int(datetime.now(timezone.utc).timestamp())}"
    sample_event = DisruptionEvent(
        event_id=event_id,
        type=event_type,
        flight={
            "carrier": "ZZ",
            "number": "ZZ123",
            "origin": "LHR",
            "destination": "JFK",
            "scheduled_departure": datetime.now(timezone.utc) + datetime.timedelta(hours=2),
            "scheduled_arrival": datetime.now(timezone.utc) + datetime.timedelta(hours=10),
            "scheduled_departure": datetime.now(timezone.utc) + timedelta(hours=2),
            "scheduled_arrival": datetime.now(timezone.utc) + timedelta(hours=10),
        },
    )
    agent = ReboundAgent(db=db)
    record = agent.run(sample_event)
    return {"record": record.model_dump(), "run_id": agent.run_id}
    return {"record": record.model_dump(), "run_id": agent.run_id, "scenario": scenario}


if os.path.exists("viewer"):
    app.mount("/viewer", StaticFiles(directory="viewer", html=True), name="viewer")


@app.get("/")
def root_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/viewer/")


