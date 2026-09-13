"""
One-time Google OAuth setup.

Reads credentials.json (the OAuth client you downloaded from GCP console),
opens a browser for you to sign in and grant scope, and writes token.json.

After this runs successfully, clients/gcal.py and clients/gmail.py will
auto-load token.json and use the live APIs. Both files are in .gitignore.

Usage:
    python3.11 scripts/setup_google_oauth.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("❌ google-auth-oauthlib is not installed.")
    print("   Run: python3.11 -m pip install -r requirements.txt")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")


def main() -> None:
    if not Path(CREDENTIALS_PATH).exists():
        print(f"❌ {CREDENTIALS_PATH} not found.")
        print("   Download the OAuth client JSON from GCP console → Credentials")
        print("   and save it to the repo root as 'credentials.json'.")
        sys.exit(1)

    print(f"Scopes requested:\n  " + "\n  ".join(SCOPES))
    print("\nOpening browser for consent…")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)

    Path(TOKEN_PATH).write_text(creds.to_json())
    print(f"\n✅ Saved refresh token to {TOKEN_PATH}")
    print("   (both credentials.json and token.json are git-ignored)")
    print("\nNext:")
    print("  python3.11 scripts/manual_calendar.py")
    print("  python3.11 scripts/manual_gmail.py")


if __name__ == "__main__":
    main()
