"""
Google Calendar sandbox sanity check.

Uses token.json (produced by scripts/setup_google_oauth.py) to prove the
Calendar API works end-to-end: create a fake trip event, patch it, read it
back, then delete it. Nothing is left on your calendar.

Usage:
    python3.11 scripts/manual_calendar.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("❌ google-api-python-client is not installed. Run pip install -r requirements.txt")
    sys.exit(1)

TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")


def step(n: int, title: str) -> None:
    print(f"\n── Step {n}: {title} " + "─" * max(1, 60 - len(title)))


def main() -> None:
    if not Path(TOKEN_PATH).exists():
        print(f"❌ {TOKEN_PATH} not found. Run scripts/setup_google_oauth.py first.")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    svc = build("calendar", "v3", credentials=creds)

    step(0, "cleanup any leftover rebound_test events")
    leftover = (
        svc.events()
        .list(calendarId="primary", privateExtendedProperty="rebound_test=true", maxResults=25)
        .execute()
        .get("items", [])
    )
    for e in leftover:
        svc.events().delete(calendarId="primary", eventId=e["id"]).execute()
    print(f"   ✓ Deleted {len(leftover)} stale test event(s).")

    start = datetime.now(timezone.utc) + timedelta(days=14, hours=10)
    end = start + timedelta(hours=6)

    step(1, "events.insert (create a temp trip event)")
    event_body = {
        "summary": "[Rebound test] ZZ123 SFO→JFK",
        "description": "Sanity check event, delete on sight.",
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
        "extendedProperties": {"private": {"rebound_test": "true"}},
    }
    created = svc.events().insert(calendarId="primary", body=event_body).execute()
    event_id = created["id"]
    print(f"   ✓ Created event id={event_id}, htmlLink={created.get('htmlLink')}")

    step(2, f"events.patch (shift start/end 30 min later)")
    new_start = start + timedelta(minutes=30)
    new_end = end + timedelta(minutes=30)
    patched = (
        svc.events()
        .patch(
            calendarId="primary",
            eventId=event_id,
            body={
                "start": {"dateTime": new_start.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": new_end.isoformat(), "timeZone": "UTC"},
                "summary": "[Rebound test] ZZ201 (rebooked) SFO→JFK",
            },
        )
        .execute()
    )
    print(f"   ✓ Patched. new summary={patched['summary']}")

    step(3, "events.get (verify patch persisted)")
    got = svc.events().get(calendarId="primary", eventId=event_id).execute()
    returned_start = datetime.fromisoformat(got["start"]["dateTime"])
    # Compare as UTC instants — Google may serialize in the calendar's local TZ.
    delta = abs((returned_start.astimezone(timezone.utc) - new_start).total_seconds())
    assert delta < 60, (
        f"start not patched: got {got['start']} (parsed {returned_start.isoformat()}), "
        f"expected {new_start.isoformat()}"
    )
    print(f"   ✓ Verified. start={got['start']['dateTime']} (matches expected within 60s)")

    step(4, "events.delete (clean up)")
    svc.events().delete(calendarId="primary", eventId=event_id).execute()
    print("   ✓ Deleted.")

    print("\n✅ Google Calendar live sandbox check passed.")
    print(f"   Event {event_id} was created, patched, verified, and deleted.")


if __name__ == "__main__":
    main()
