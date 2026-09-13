"""
Google Calendar Client
Interacts with the Google Calendar API via OAuth2 credentials (token.json).
Identifies destination commitments to determine the hard deadline, and patches
trip events upon successful rebooking.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from clients.base import BaseClient, RetryableAPIError

logger = logging.getLogger("rebound.gcal")


class GoogleCalendarClient(BaseClient):
    """Client for Google Calendar API operations."""
    def __init__(
        self,
        token_path: str = "token.json",
        credentials_path: str = "credentials.json",
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.token_path = os.getenv("GOOGLE_TOKEN_PATH", token_path)
        self.credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", credentials_path)
        self.service = None

        if not self.dry_run and os.path.exists(self.token_path):
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                
                creds = Credentials.from_authorized_user_file(self.token_path)
                self.service = build("calendar", "v3", credentials=creds)
            except Exception as e:
                logger.warning("Failed to initialize live Google Calendar service: %s", e)

    def get_destination_deadline(
        self,
        trip_arrival: datetime,
        default_buffer_hours: float = 4.0,
        calendar_id: str = "primary",
    ) -> datetime:
        """
        Queries the user's destination day calendar.
        The earliest non-flight event (e.g. key meeting) defines the hard arrival deadline.
        Falls back to trip_arrival + default_buffer_hours if no commitments exist.
        """
        fallback_deadline = trip_arrival + timedelta(hours=default_buffer_hours)
        if self.dry_run or not self.service:
            logger.info("[DRY RUN / Fallback] Using deadline based on default buffer: %s", fallback_deadline.isoformat())
            return fallback_deadline

        try:
            # Query from arrival to end of next day
            time_min = trip_arrival.isoformat()
            time_max = (trip_arrival + timedelta(days=1)).replace(hour=23, minute=59, second=59).isoformat()

            events_result = self.service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            items = events_result.get("items", [])
            for item in items:
                summary = item.get("summary", "").lower()
                # Exclude existing flight cards
                if "flight" in summary or "zz" in summary:
                    continue

                start_str = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
                if start_str:
                    try:
                        event_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        if event_start > trip_arrival:
                            logger.info("Found destination commitment '%s' at %s (Hard Deadline)", item.get("summary"), start_str)
                            return event_start
                    except Exception:
                        continue

            return fallback_deadline

        except Exception as e:
            logger.error("Error reading Google Calendar destination commitments: %s", e)
            self.record_retry("calendar.get")
            return fallback_deadline

    def patch_trip_event(
        self,
        event_id: str,
        new_start: datetime,
        new_end: datetime,
        booking_ref: str,
        summary: Optional[str] = None,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        """Patches an existing flight event with the new departure/arrival and booking reference."""
        new_summary = summary or f"Flight (Rebooked: {booking_ref})"
        if self.dry_run or not self.service:
            logger.info("[DRY RUN] Would patch Calendar event %s -> start %s, ref %s", event_id, new_start.isoformat(), booking_ref)
            return {
                "id": event_id,
                "summary": new_summary,
                "start": {"dateTime": new_start.isoformat()},
                "end": {"dateTime": new_end.isoformat()},
                "status": "updated_dry_run",
            }

        try:
            body = {
                "summary": new_summary,
                "description": f"Automatically rebooked by Rebound. New booking reference: {booking_ref}",
                "start": {"dateTime": new_start.isoformat()},
                "end": {"dateTime": new_end.isoformat()},
            }
            updated_event = self.service.events().patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=body,
            ).execute()
            logger.info("Successfully patched Google Calendar event %s", event_id)
            return updated_event
        except Exception as e:
            logger.error("Failed to patch Google Calendar event %s: %s", event_id, e)
            self.record_retry("calendar.patch")
            raise RetryableAPIError(f"Google Calendar patch failed: {e}", status_code=503)

