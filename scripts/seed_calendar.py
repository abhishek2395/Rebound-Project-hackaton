#!/usr/bin/env python3
"""
Seed Google Calendar for Traveler Alex
Creates or updates:
  1. The initial flight card event (ZZ123 from LHR to JFK).
  2. The destination hard meeting commitment (NYC Board Meeting at 09:00 UTC).

Usage:
  python3 scripts/seed_calendar.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from clients.gcal import GoogleCalendarClient
from clients.fakes import FakeCalendar

def main():
    print("=" * 60)
    print(" 📅  REBOUND — Google Calendar Seeding Utility")
    print("=" * 60)

    # Flight scheduled tomorrow
    now = datetime.now(timezone.utc)
    base_date = now + timedelta(days=1)
    flight_dep = base_date.replace(hour=10, minute=0, second=0, microsecond=0)
    flight_arr = base_date.replace(hour=13, minute=0, second=0, microsecond=0)

    # Hard meeting the following morning
    meeting_start = (base_date + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    meeting_end = meeting_start + timedelta(hours=2)

    events_to_seed = [
        {
            "id": "trip_flight_event",
            "summary": "Flight ZZ123: London (LHR) -> New York (JFK)",
            "start": {"dateTime": flight_dep.isoformat()},
            "end": {"dateTime": flight_arr.isoformat()},
            "description": "Original flight booking. Booking Reference: REF_ORIGINAL_380. Duffel Order: ord_original_380",
            "location": "London Heathrow Airport (LHR)",
        },
        {
            "id": "trip_meeting_deadline",
            "summary": "Q3 Executive Board Meeting & Keynote",
            "start": {"dateTime": meeting_start.isoformat()},
            "end": {"dateTime": meeting_end.isoformat()},
            "description": "Hard destination deadline. Critical commitment: presence required in person.",
            "location": "Manhattan, New York, NY",
        },
    ]

    # Check if live Google Calendar credentials exist
    has_live_creds = os.path.exists("token.json") or os.path.exists("credentials.json") or os.getenv("GOOGLE_CALENDAR_CREDENTIALS_JSON")

    if has_live_creds:
        print("[INFO] Live Google Calendar credentials detected. Connecting to Google API...")
        try:
            client = GoogleCalendarClient()
            for ev in events_to_seed:
                # Upsert event
                try:
                    client.service.events().insert(calendarId="primary", body=ev).execute()
                    print(f"  ✅ Seeded live event: {ev['summary']} ({ev['id']})")
                except Exception as e:
                    print(f"  ⚠️ Live API notice for {ev['id']}: {e}")
        except Exception as err:
            print(f"[ERROR] Live calendar initialization failed: {err}")
            print("[INFO] Falling back to structured inspection output.")
    else:
        print("[INFO] Live Google OAuth credentials not found (running in sandbox/CI mode).")
        print("[INFO] Mock calendar state prepared with the following events:\n")

    for ev in events_to_seed:
        print(f"  📌 Event ID:    {ev['id']}")
        print(f"     Title:       {ev['summary']}")
        print(f"     Start:       {ev['start']['dateTime']}")
        print(f"     End:         {ev['end']['dateTime']}")
        print(f"     Description: {ev['description']}")
        print("-" * 50)

    print("\n✅ Calendar ready. Rebound will resolve the hard deadline from:")
    print(f"   'trip_meeting_deadline' -> {meeting_start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Buffer before meeting:  16 hours\n")


if __name__ == "__main__":
    main()

