"""
Gmail sandbox sanity check.

Uses token.json (produced by scripts/setup_google_oauth.py) to prove the
Gmail API works end-to-end: creates a draft with the exact itinerary
template Rebound uses, verifies it landed in the Drafts folder, then deletes
the draft. Nothing is actually sent.

Usage:
    python3.11 scripts/manual_gmail.py
"""

from __future__ import annotations

import base64
import os
import sys
from email.mime.text import MIMEText
from pathlib import Path

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("❌ google-api-python-client is not installed. Run pip install -r requirements.txt")
    sys.exit(1)

TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")

ITINERARY = """Your original flight ZZ123 was cancelled. I booked:

  ZZ201  SFO 14:10 → JFK 22:35  Nonstop  Economy
  Booking ref: 3ZPLMP  (Duffel order ord_test)
  Cost difference: +$142.50 (auto-book, within threshold)

Calendar updated. Sam has been told your new ETA.

— Rebound (sanity check draft — delete on sight)
"""


def step(n: int, title: str) -> None:
    print(f"\n── Step {n}: {title} " + "─" * max(1, 60 - len(title)))


def main() -> None:
    if not Path(TOKEN_PATH).exists():
        print(f"❌ {TOKEN_PATH} not found. Run scripts/setup_google_oauth.py first.")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    svc = build("gmail", "v1", credentials=creds)

    profile = svc.users().getProfile(userId="me").execute()
    me = profile["emailAddress"]
    print(f"Authorized as: {me}")

    step(1, "drafts.create (build MIME itinerary draft)")
    msg = MIMEText(ITINERARY)
    msg["To"] = me
    msg["From"] = me
    msg["Subject"] = "[Rebound test] Rebooked: ZZ201 SFO→JFK"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = (
        svc.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    draft_id = draft["id"]
    print(f"   ✓ Draft created: id={draft_id}")

    step(2, "drafts.get (verify draft persisted)")
    got = svc.users().drafts().get(userId="me", id=draft_id, format="metadata").execute()
    subj = next(
        (h["value"] for h in got["message"]["payload"]["headers"] if h["name"] == "Subject"),
        None,
    )
    print(f"   ✓ Verified. Subject: {subj}")

    step(3, "drafts.delete (clean up)")
    svc.users().drafts().delete(userId="me", id=draft_id).execute()
    print("   ✓ Deleted.")

    print("\n✅ Gmail live sandbox check passed.")
    print(f"   Draft {draft_id} was created, verified, and deleted.")


if __name__ == "__main__":
    main()
