"""
Twilio SMS Gateway Client
Dispatches outbound SMS messages for human-in-the-loop approvals, escalation alerts,
and pickup contact notifications via the Twilio REST API over standard HTTPS.
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from clients.base import BaseClient, NonRetryableAPIError, RetryableAPIError

logger = logging.getLogger("rebound.twilio")


class TwilioClient(BaseClient):
    """Client for Twilio SMS operations."""
    def __init__(
        self,
        account_sid: Optional[str] = None,
        auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.account_sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token = auth_token or os.getenv("TWILIO_AUTH_TOKEN", "")
        self.from_number = from_number or os.getenv("TWILIO_FROM_NUMBER", "")
        self.timeout = httpx.Timeout(10.0)

    @retry(
        retry=retry_if_exception_type(RetryableAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=4),
        reraise=True,
    )
    def send_sms(self, to_phone: str, body: str, sms_type: str = "summary") -> Dict[str, Any]:
        """Dispatches an SMS message using Twilio's HTTP API."""
        if self.dry_run or not (self.account_sid and self.auth_token):
            logger.info("[DRY RUN] Would send Twilio SMS to %s (%s):\n%s", to_phone, sms_type, body)
            return {
                "sid": f"SM_dry_{int(os.times().elapsed * 1000)}",
                "to": to_phone,
                "body": body,
                "status": "queued_dry_run",
                "type": sms_type,
            }

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = {
            "To": to_phone,
            "From": self.from_number,
            "Body": body,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url,
                    data=data,
                    auth=(self.account_sid, self.auth_token),
                )

            if resp.status_code in (500, 502, 503, 504, 429):
                self.record_retry("twilio.send")
                raise RetryableAPIError(f"Twilio gateway error {resp.status_code}: {resp.text}", status_code=resp.status_code)
            elif resp.status_code >= 400:
                raise NonRetryableAPIError(f"Twilio rejected message {resp.status_code}: {resp.text}", status_code=resp.status_code)

            logger.info("Successfully dispatched Twilio SMS to %s (%s)", to_phone, sms_type)
            return resp.json()

        except httpx.RequestError as e:
            self.record_retry("twilio.send")
            raise RetryableAPIError(f"Twilio network connection error: {e}", status_code=503)

