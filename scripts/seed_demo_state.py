"""
Seed real state for a clean end-to-end demo.

Books one real Duffel test order (the "original" ticket that will get
cancelled during the demo), and creates one real Google Calendar event
(the flight card that will get patched). Prints two `export` lines to
paste into your shell so /api/demo/trigger uses these real IDs instead
of the fixture's placeholders.

    python3.11 scripts/seed_demo_state.py

Requires DUFFEL_API_KEY and token.json to be present.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("❌ google-api-python-client is not installed. pip install -r requirements.txt")
    sys.exit(1)

DUFFEL_API_URL = "https://api.duffel.com/air"
API_KEY = os.getenv("DUFFEL_API_KEY", "")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
SEEDS_FILE = ".demo_seeds.json"


def die(msg: str) -> None:
    print(f"\n❌ {msg}")
    sys.exit(1)


def step(n: int, title: str) -> None:
    print(f"\n── Step {n}: {title} " + "─" * max(1, 60 - len(title)))


def seed_duffel_order() -> tuple[str, str]:
    if not API_KEY.startswith("duffel_test_"):
        die("DUFFEL_API_KEY missing or not a test key.")

    depart = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Duffel-Version": "v2",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Route is configurable — the demo will search/rebook whatever route
    # you seed, because /api/demo/trigger reads it back from the order.
    origin = os.getenv("DEMO_SEED_ORIGIN", "SFO")
    destination = os.getenv("DEMO_SEED_DESTINATION", "JFK")

    step(1, f"Book real Duffel 'original' order ({origin} → {destination})")
    with httpx.Client(timeout=60.0, headers=headers) as client:
        r = client.post(
            f"{DUFFEL_API_URL}/offer_requests?return_offers=true",
            json={
                "data": {
                    "slices": [{"origin": origin, "destination": destination, "departure_date": depart}],
                    "passengers": [{"type": "adult"}],
                    "cabin_class": "economy",
                }
            },
        )
        r.raise_for_status()
        offers = r.json()["data"]["offers"]
        zz = [o for o in offers if o["owner"]["iata_code"] == "ZZ"] or offers
        offer = sorted(zz, key=lambda o: float(o["total_amount"]))[0]
        print(f"   Picked offer {offer['id']} @ {offer['total_amount']} {offer['total_currency']}")

        passenger_id = offer["passengers"][0]["id"]
        r = client.post(
            f"{DUFFEL_API_URL}/orders",
            json={
                "data": {
                    "type": "instant",
                    "selected_offers": [offer["id"]],
                    "passengers": [
                        {
                            "id": passenger_id,
                            "title": "mr",
                            "given_name": "Alex",
                            "family_name": "Rivera",
                            "born_on": "1990-01-01",
                            "gender": "m",
                            "email": "alex@example.com",
                            "phone_number": "+14158675309",
                        }
                    ],
                    "payments": [
                        {
                            "type": "balance",
                            "amount": offer["total_amount"],
                            "currency": offer["total_currency"],
                        }
                    ],
                }
            },
        )
        r.raise_for_status()
        order = r.json()["data"]
        total_amount = float(order.get("total_amount") or offer["total_amount"])
        currency = order.get("total_currency") or offer["total_currency"]
        print(f"   ✓ Order {order['id']}  booking_ref={order.get('booking_reference')}  paid={total_amount} {currency}")
        return order["id"], order.get("booking_reference", ""), total_amount


def seed_calendar_events(order_id: str, booking_ref: str) -> str:
    if not Path(TOKEN_PATH).exists():
        die(f"{TOKEN_PATH} not found. Run scripts/setup_google_oauth.py first.")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    svc = build("calendar", "v3", credentials=creds)

    step(2, "cleanup any prior rebound_demo events")
    prior = (
        svc.events()
        .list(calendarId="primary", privateExtendedProperty="rebound_demo=true", maxResults=25)
        .execute()
        .get("items", [])
    )
    for e in prior:
        svc.events().delete(calendarId="primary", eventId=e["id"]).execute()
    print(f"   ✓ Deleted {len(prior)} stale demo event(s).")

    # A destination meeting the day after departure — this is the hard deadline
    dep = datetime.now(timezone.utc) + timedelta(days=14, hours=10)
    meeting_start = dep.replace(hour=15, minute=0, second=0) + timedelta(days=1)
    meeting_end = meeting_start + timedelta(hours=1)

    step(3, "Create destination commitment (deadline anchor)")
    meeting = (
        svc.events()
        .insert(
            calendarId="primary",
            body={
                "summary": "[Rebound demo] Key stakeholder meeting @ JFK",
                "location": "New York, NY",
                "description": f"Rebound demo destination commitment. Flight ZZ123 ({order_id}).",
                "start": {"dateTime": meeting_start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": meeting_end.isoformat(), "timeZone": "UTC"},
                "extendedProperties": {"private": {"rebound_demo": "true"}},
            },
        )
        .execute()
    )
    print(f"   ✓ Meeting event {meeting['id']}")

    step(4, "Create the trip flight card (this is what gets patched)")
    flight_start = dep
    flight_end = dep + timedelta(hours=6)
    flight = (
        svc.events()
        .insert(
            calendarId="primary",
            body={
                "summary": f"[Rebound demo] Flight ZZ123 SFO→JFK",
                "description": (
                    f"Rebound demo flight card. Booking ref {booking_ref}. "
                    f"Duffel order {order_id}. Rebound will patch this on rebook."
                ),
                "start": {"dateTime": flight_start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": flight_end.isoformat(), "timeZone": "UTC"},
                "extendedProperties": {
                    "private": {"rebound_demo": "true", "duffel_order_id": order_id}
                },
            },
        )
        .execute()
    )
    print(f"   ✓ Flight card event {flight['id']}")
    return flight["id"]


def main() -> None:
    order_id, booking_ref, total_amount = seed_duffel_order()
    flight_event_id = seed_calendar_events(order_id, booking_ref)

    seeds = {
        "DEMO_ORIGINAL_ORDER_ID": order_id,
        "DEMO_ORIGINAL_BOOKING_REF": booking_ref,
        "DEMO_ORIGINAL_TOTAL": total_amount,
        "DEMO_CALENDAR_EVENT_ID": flight_event_id,
        "seeded_at": datetime.now(timezone.utc).isoformat(),
    }
    Path(SEEDS_FILE).write_text(json.dumps(seeds, indent=2))

    print("\n✅ Real state seeded.")
    print(f"   Seeds saved to {SEEDS_FILE} (git-ignored).")
    print("\nExport these in the shell that will run the server, then restart:")
    print(f"   export DEMO_ORIGINAL_ORDER_ID={order_id}")
    print(f"   export DEMO_ORIGINAL_TOTAL={total_amount}")
    print(f"   export DEMO_CALENDAR_EVENT_ID={flight_event_id}")
    print("\nTo GUARANTEE the WhatsApp approval flow (S02) fires regardless of live")
    print("Duffel prices, also export a low approval threshold, e.g.:")
    print("   export DEMO_APPROVAL_THRESHOLD=25")


if __name__ == "__main__":
    main()
