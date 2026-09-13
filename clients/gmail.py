"""
Gmail Client
Interacts with the Gmail API via OAuth2 credentials.
Sends confirmation itinerary emails and drafts EU261 / DOT compensation claims.
"""

import base64
import logging
import os
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

from clients.base import BaseClient, RetryableAPIError

logger = logging.getLogger("rebound.gmail")


class GmailClient(BaseClient):
    """Client for Gmail API operations."""
    def __init__(
        self,
        token_path: str = "token.json",
        credentials_path: str = "credentials.json",
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.token_path = os.getenv("GOOGLE_TOKEN_PATH", token_path)
        self.service = None

        if not self.dry_run and os.path.exists(self.token_path):
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build

                creds = Credentials.from_authorized_user_file(self.token_path)
                self.service = build("gmail", "v1", credentials=creds)
            except Exception as e:
                logger.warning("Failed to initialize live Gmail service: %s", e)

    def _create_message(self, to_email: str, subject: str, body: str) -> Dict[str, str]:
        message = MIMEText(body)
        message["to"] = to_email
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        return {"raw": raw}

    def send_itinerary_email(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        """Sends plain-text itinerary confirmation email to traveler."""
        if self.dry_run or not self.service:
            logger.info("[DRY RUN] Would send itinerary email to %s: '%s'", to_email, subject)
            return {"status": "sent_dry_run", "to": to_email, "subject": subject}

        try:
            raw_msg = self._create_message(to_email, subject, body)
            result = self.service.users().messages().send(userId="me", body=raw_msg).execute()
            logger.info("Successfully sent Gmail itinerary message %s to %s", result.get("id"), to_email)
            return result
        except Exception as e:
            logger.error("Failed to send Gmail itinerary to %s: %s", to_email, e)
            self.record_retry("gmail.send")
            raise RetryableAPIError(f"Gmail send failed: {e}", status_code=503)

    def create_compensation_draft(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        """Drafts a statutory EU261/DOT compensation claim in the traveler's Gmail drafts."""
        if self.dry_run or not self.service:
            logger.info("[DRY RUN] Would create EU261 compensation draft for %s: '%s'", to_email, subject)
            return {"status": "draft_created_dry_run", "subject": subject}

        try:
            raw_msg = self._create_message(to_email, subject, body)
            draft_body = {"message": raw_msg}
            result = self.service.users().drafts().create(userId="me", body=draft_body).execute()
            logger.info("Successfully created Gmail claim draft %s", result.get("id"))
            return result
        except Exception as e:
            logger.error("Failed to create Gmail claim draft: %s", e)
            self.record_retry("gmail.draft")
            raise RetryableAPIError(f"Gmail draft creation failed: {e}", status_code=503)

