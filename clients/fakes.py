"""
High-Fidelity In-Memory Recording Fakes
Used in testing and CI to simulate Duffel, Google Calendar, Gmail, and Twilio.
Supports call recording, fault injection (500s, 503s, timeouts, expired offers),
and verification assertions.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from agent.models import (
    CabinClass,
    CalendarEvent,
    CancellationQuote,
    ConfirmedBooking,
    FlightOffer,
    HoldOrder,
    TravelerProfile,
)
from clients.base import BaseClient, NonRetryableAPIError, RetryableAPIError


class FakeDuffel(BaseClient):
    """
    In-memory simulation of the Duffel NDC Flight API.
    Implements search, hold orders, ticket confirmation, verification, and cancellations.
    """
    def __init__(
        self,
        offers: Optional[List[FlightOffer]] = None,
        failures: Optional[List[Dict[str, Any]]] = None,
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.available_offers: List[FlightOffer] = offers or []
        self.failures_config: List[Dict[str, Any]] = failures or []
        
        # State stores
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.cancellations: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Dict[str, Any]] = []
        
        # Track failure sequences per operation
        self._failure_counters: Dict[str, int] = {}

    def _check_and_trigger_failure(self, operation_name: str) -> None:
        """Evaluates injected failure rules for the given operation."""
        for failure_rule in self.failures_config:
            if failure_rule.get("call") == operation_name:
                sequence = failure_rule.get("sequence", [])
                counter = self._failure_counters.get(operation_name, 0)
                self._failure_counters[operation_name] = counter + 1

                if counter < len(sequence):
                    code = sequence[counter]
                    if code == "500":
                        self.record_retry(operation_name)
                        raise RetryableAPIError(
                            f"Duffel simulated HTTP 500 on {operation_name}",
                            status_code=500,
                        )
                    elif code == "429":
                        self.record_retry(operation_name)
                        raise RetryableAPIError(
                            f"Duffel simulated HTTP 429 Rate Limit on {operation_name}",
                            status_code=429,
                        )
                    elif code == "offer_expired":
                        raise NonRetryableAPIError(
                            "Simulated offer_expired: selected offer is no longer valid",
                            status_code=400,
                        )

    def search_offers(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        passengers: int = 1,
        cabin_class: str = "economy",
    ) -> List[FlightOffer]:
        """Simulates POST /air/offer_requests and reading returned offers."""
        self._check_and_trigger_failure("offer_requests.create")
        self.calls.append({
            "method": "search_offers",
            "args": {
                "origin": origin,
                "destination": destination,
                "departure_date": departure_date,
                "passengers": passengers,
                "cabin_class": cabin_class,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return self.available_offers

    def create_hold_order(
        self,
        offer_id: str,
        profile: TravelerProfile,
    ) -> HoldOrder:
        """
        Simulates POST /air/orders with payments omitted (Hold Order Pattern).
        Locks price and seat inventory for 15-30 minutes.
        """
        self._check_and_trigger_failure("orders.create_hold")
        
        # Verify offer exists
        matching = [o for o in self.available_offers if o.id == offer_id]
        amount = matching[0].total_amount if matching else 500.00
        currency = matching[0].currency if matching else "USD"

        order_id = f"ord_hold_{offer_id}"
        hold_order = HoldOrder(
            order_id=order_id,
            offer_id=offer_id,
            total_amount=amount,
            currency=currency,
            price_guarantee_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            status="hold",
        )
        self.orders[order_id] = hold_order.model_dump()
        self.calls.append({
            "method": "create_hold_order",
            "offer_id": offer_id,
            "order_id": order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return hold_order

    def confirm_booking(
        self,
        offer_id: str,
        profile: TravelerProfile,
        hold_order_id: Optional[str] = None,
        simulate_verify_mismatch: bool = False,
    ) -> ConfirmedBooking:
        """
        Simulates POST /air/payments on a held order or direct POST /air/orders.
        """
        self._check_and_trigger_failure("orders.create")

        # Check for offer expiration failure injection
        for rule in self.failures_config:
            if rule.get("trigger") == "offer_expired":
                raise NonRetryableAPIError("offer_expired: selected offer is no longer valid", status_code=400)

        order_id = f"ord_{offer_id}_{int(datetime.now(timezone.utc).timestamp())}"
        if simulate_verify_mismatch:
            order_id = "ord_corrupted_mismatch"

        matching = [o for o in self.available_offers if o.id == offer_id]
        amount = matching[0].total_amount if matching else 500.00
        currency = matching[0].currency if matching else "USD"

        booking = ConfirmedBooking(
            order_id=order_id,
            booking_reference=f"REF{order_id[-6:].upper()}",
            offer_id=offer_id,
            total_amount=amount,
            currency=currency,
            passenger_name=profile.name,
            passenger_email=profile.email,
            confirmed_at=datetime.now(timezone.utc),
        )

        self.orders[order_id] = {
            **booking.model_dump(),
            "status": "confirmed",
        }
        self.calls.append({
            "method": "confirm_booking",
            "order_id": order_id,
            "offer_id": offer_id,
            "hold_order_id": hold_order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return booking

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Simulates GET /air/orders/{id} for the verification step."""
        self._check_and_trigger_failure("orders.get")
        self.calls.append({
            "method": "get_order",
            "order_id": order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if order_id in self.orders:
            return self.orders[order_id]
        return {"id": order_id, "status": "unknown"}

    def request_cancellation_quote(self, order_id: str, original_amount: float = 380.0) -> CancellationQuote:
        """Simulates POST /air/order_cancellations to obtain refund quote."""
        self._check_and_trigger_failure("order_cancellations.create")
        cancellation_id = f"ore_cxl_{order_id}"
        quote = CancellationQuote(
            order_id=order_id,
            cancellation_id=cancellation_id,
            refund_amount=original_amount,
            refund_currency="USD",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        self.cancellations[cancellation_id] = quote.model_dump()
        self.calls.append({
            "method": "request_cancellation_quote",
            "order_id": order_id,
            "cancellation_id": cancellation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return quote

    def confirm_cancellation(self, cancellation_id: str) -> bool:
        """Simulates POST /air/order_cancellations/{id}/actions/confirm."""
        self._check_and_trigger_failure("order_cancellations.confirm")
        if cancellation_id in self.cancellations:
            quote = self.cancellations[cancellation_id]
            order_id = quote["order_id"]
            if order_id in self.orders:
                self.orders[order_id]["status"] = "cancelled"
                self.orders[order_id]["cancelled_at"] = datetime.now(timezone.utc).isoformat()
            else:
                self.orders[order_id] = {
                    "id": order_id,
                    "status": "cancelled",
                    "cancelled_at": datetime.now(timezone.utc).isoformat(),
                }
            self.calls.append({
                "method": "confirm_cancellation",
                "cancellation_id": cancellation_id,
                "order_id": order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return True
        return False


class FakeCalendar(BaseClient):
    """In-memory simulation of Google Calendar API."""
    def __init__(
        self,
        deadline: Optional[datetime] = None,
        conflicts: Optional[List[Dict[str, Any]]] = None,
        injected_failure: Optional[str] = None,
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.deadline = deadline or datetime.fromisoformat("2026-09-15T09:00:00Z")
        self.conflicts = conflicts or []
        self.injected_failure = injected_failure
        
        self.events: Dict[str, Dict[str, Any]] = {}
        self.calls: List[Dict[str, Any]] = []

    def get_destination_deadline(
        self,
        trip_arrival: datetime,
        default_buffer_hours: float = 4.0,
    ) -> datetime:
        """Returns the hard destination commitment deadline."""
        if self.injected_failure == "503":
            self.record_retry("calendar.get")
            raise RetryableAPIError("Google Calendar 503 Service Unavailable", status_code=503)

        self.calls.append({
            "method": "get_destination_deadline",
            "trip_arrival": trip_arrival.isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return self.deadline

    def patch_trip_event(
        self,
        event_id: str,
        new_start: datetime,
        new_end: datetime,
        booking_ref: str,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Patches flight event on Google Calendar."""
        if self.injected_failure == "503":
            self.record_retry("calendar.patch")
            raise RetryableAPIError("Google Calendar 503 Service Unavailable", status_code=503)

        event = {
            "id": event_id,
            "summary": summary or f"Flight (Rebooked: {booking_ref})",
            "start": new_start.isoformat(),
            "end": new_end.isoformat(),
            "booking_reference": booking_ref,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.events[event_id] = event
        self.calls.append({
            "method": "patch_trip_event",
            "event_id": event_id,
            "booking_ref": booking_ref,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return event


class FakeGmail(BaseClient):
    """In-memory simulation of Gmail API."""
    def __init__(
        self,
        injected_failure: Optional[str] = None,
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.injected_failure = injected_failure
        self.sent_emails: List[Dict[str, Any]] = []
        self.drafts: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []

    def send_itinerary_email(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        if self.injected_failure == "503":
            self.record_retry("gmail.send")
            raise RetryableAPIError("Gmail 503 Service Unavailable", status_code=503)

        record = {
            "to": to_email,
            "subject": subject,
            "body": body,
            "type": "itinerary",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sent_emails.append(record)
        self.calls.append({"method": "send_itinerary_email", "to": to_email, "subject": subject})
        return record

    def create_compensation_draft(self, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        if self.injected_failure == "503":
            self.record_retry("gmail.draft")
            raise RetryableAPIError("Gmail 503 Service Unavailable", status_code=503)

        record = {
            "to": to_email,
            "subject": subject,
            "body": body,
            "type": "eu261_claim",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.drafts.append(record)
        self.calls.append({"method": "create_compensation_draft", "to": to_email, "subject": subject})
        return record


class FakeTwilio(BaseClient):
    """In-memory simulation of Twilio SMS Gateway."""
    def __init__(
        self,
        injected_failure: Optional[str] = None,
        dry_run: bool = False,
    ):
        super().__init__(dry_run=dry_run)
        self.injected_failure = injected_failure
        self.sent_messages: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []

    def send_sms(self, to_phone: str, body: str, sms_type: str = "summary") -> Dict[str, Any]:
        if self.injected_failure == "500":
            self.record_retry("twilio.send")
            raise RetryableAPIError("Twilio SMS Delivery Failure 500", status_code=500)

        record = {
            "to": to_phone,
            "body": body,
            "type": sms_type,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.sent_messages.append(record)
        self.calls.append({"method": "send_sms", "to": to_phone, "type": sms_type})
        return record
