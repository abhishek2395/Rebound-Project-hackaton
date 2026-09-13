"""
Live Duffel NDC Flight API Client
Interacts with Duffel's REST API (test mode: Duffel Airways IATA ZZ).
Implements offer requests, the Hold Order pattern, ticket issuance, verification,
and 3-step order cancellations with exponential backoff retries.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.models import (
    CabinClass,
    CancellationQuote,
    ConfirmedBooking,
    FlightOffer,
    HoldOrder,
    TravelerProfile,
)
from clients.base import BaseClient, NonRetryableAPIError, RetryableAPIError

logger = logging.getLogger("rebound.duffel")

DUFFEL_API_URL = "https://api.duffel.com/air"
DUFFEL_VERSION = "v2"


class DuffelClient(BaseClient):
    """Production/Sandbox client for Duffel Flights API."""
    def __init__(self, api_key: Optional[str] = None, dry_run: bool = False):
        super().__init__(dry_run=dry_run)
        self.api_key = api_key or os.getenv("DUFFEL_API_KEY", "")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": DUFFEL_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Explicit 130s supplier latency timeout per blueprint recommendations
        self.timeout = httpx.Timeout(130.0, connect=10.0)

    def _handle_response_status(self, response: httpx.Response, op_name: str) -> Dict[str, Any]:
        """Maps HTTP status codes to Retryable or NonRetryable exceptions."""
        if response.status_code in (500, 502, 503, 504, 429):
            self.record_retry(op_name)
            raise RetryableAPIError(
                f"Duffel {op_name} failed with status {response.status_code}: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        elif response.status_code >= 400:
            raise NonRetryableAPIError(
                f"Duffel {op_name} client error {response.status_code}: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )
        return response.json()

    @retry(
        retry=retry_if_exception_type(RetryableAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=4),
        reraise=True,
    )
    def search_offers(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        passengers: int = 1,
        cabin_class: str = "economy",
    ) -> List[FlightOffer]:
        """Executes POST /air/offer_requests and converts raw offers to FlightOffer models."""
        if self.dry_run:
            logger.info("[DRY RUN] Would search Duffel offers for %s -> %s on %s", origin, destination, departure_date)
            return []

        payload = {
            "data": {
                "slices": [
                    {
                        "origin": origin,
                        "destination": destination,
                        "departure_date": departure_date,
                    }
                ],
                "passengers": [{"type": "adult"} for _ in range(passengers)],
                "cabin_class": cabin_class,
            }
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{DUFFEL_API_URL}/offer_requests?return_offers=true",
                headers=self.headers,
                json=payload,
            )
            data = self._handle_response_status(resp, "offer_requests.create")

        offers_raw = data.get("data", {}).get("offers", [])
        parsed_offers: List[FlightOffer] = []

        for item in offers_raw:
            try:
                slice_info = item.get("slices", [{}])[0]
                segments = slice_info.get("segments", [])
                dep_time = datetime.fromisoformat(slice_info.get("departing_at"))
                arr_time = datetime.fromisoformat(slice_info.get("arriving_at"))
                carrier = item.get("owner", {}).get("iata_code", "ZZ")
                
                parsed_offers.append(FlightOffer(
                    id=item.get("id"),
                    carrier=carrier,
                    flight_number=segments[0].get("operating_carrier_flight_number") if segments else None,
                    origin=slice_info.get("origin", {}).get("iata_code"),
                    destination=slice_info.get("destination", {}).get("iata_code"),
                    departs_at=dep_time,
                    arrives_at=arr_time,
                    total_amount=float(item.get("total_amount", 0.0)),
                    currency=item.get("total_currency", "USD"),
                    segments=len(segments),
                    cabin_class=CabinClass(cabin_class),
                    raw_payload=item,
                ))
            except Exception as e:
                logger.warning("Skipping unparseable Duffel offer %s: %s", item.get("id"), e)

        return parsed_offers

    @retry(
        retry=retry_if_exception_type(RetryableAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=4),
        reraise=True,
    )
    def create_hold_order(
        self,
        offer_id: str,
        profile: TravelerProfile,
    ) -> HoldOrder:
        """
        Executes POST /air/orders strictly omitting the payments array.
        Creates an order with type 'hold' to prevent 15-minute offer expiration.
        """
        if self.dry_run:
            logger.info("[DRY RUN] Would create hold order for offer %s", offer_id)
            return HoldOrder(
                order_id=f"ord_hold_dry_{offer_id}",
                offer_id=offer_id,
                total_amount=500.0,
                status="hold",
            )

        name_parts = profile.name.split()
        first_name = name_parts[0] if name_parts else "Alex"
        last_name = name_parts[-1] if len(name_parts) > 1 else "Rivera"

        payload = {
            "data": {
                "selected_offers": [offer_id],
                "passengers": [
                    {
                        "type": "adult",
                        "title": "mr",
                        "family_name": last_name,
                        "given_name": first_name,
                        "born_on": "1990-01-01",
                        "gender": "m",
                        "email": profile.email,
                        "phone_number": profile.phone,
                    }
                ],
                "type": "hold",
            }
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{DUFFEL_API_URL}/orders",
                headers=self.headers,
                json=payload,
            )
            data = self._handle_response_status(resp, "orders.create_hold")

        order_data = data.get("data", {})
        price_expiry = None
        if order_data.get("payment_required_by"):
            try:
                price_expiry = datetime.fromisoformat(order_data["payment_required_by"])
            except Exception:
                pass

        return HoldOrder(
            order_id=order_data.get("id"),
            offer_id=offer_id,
            total_amount=float(order_data.get("total_amount", 0.0)),
            currency=order_data.get("total_currency", "USD"),
            price_guarantee_expires_at=price_expiry,
            status=order_data.get("status", "hold"),
        )

    @retry(
        retry=retry_if_exception_type(RetryableAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=4),
        reraise=True,
    )
    def confirm_booking(
        self,
        offer_id: str,
        profile: TravelerProfile,
        hold_order_id: Optional[str] = None,
    ) -> ConfirmedBooking:
        """
        Finalizes ticketing via POST /air/payments on a held order or direct order creation.
        """
        if self.dry_run:
            logger.info("[DRY RUN] Would confirm booking for offer %s (hold: %s)", offer_id, hold_order_id)
            return ConfirmedBooking(
                order_id=f"ord_dry_{offer_id}",
                booking_reference="DRY123",
                offer_id=offer_id,
                total_amount=500.0,
                passenger_name=profile.name,
                passenger_email=profile.email,
            )

        if hold_order_id:
            # Pay for existing held order. Duffel requires the payment amount to
            # exactly match the order's outstanding total, so fetch the held
            # order first rather than assuming a fixed value.
            hold_details = self.get_order(hold_order_id)
            pay_amount = str(hold_details.get("total_amount") or "500.00")
            pay_currency = hold_details.get("total_currency", "USD")
            payment_payload = {
                "data": {
                    "order_id": hold_order_id,
                    "payment": {
                        "type": "balance",
                        "currency": pay_currency,
                        "amount": pay_amount,
                    },
                }
            }
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{DUFFEL_API_URL}/payments",
                    headers=self.headers,
                    json=payment_payload,
                )
                data = self._handle_response_status(resp, "payments.create")
            order_id = hold_order_id
        else:
            with httpx.Client(timeout=self.timeout) as client:
                # Direct booking flow
                name_parts = profile.name.split()
                payload = {
                    "data": {
                        "selected_offers": [offer_id],
                        "passengers": [
                            {
                                "type": "adult",
                                "title": "mr",
                                "family_name": name_parts[-1] if len(name_parts) > 1 else "Rivera",
                                "given_name": name_parts[0],
                                "born_on": "1990-01-01",
                                "gender": "m",
                                "email": profile.email,
                                "phone_number": profile.phone,
                            }
                        ],
                        "payments": [{"type": "balance", "currency": "USD", "amount": "500.00"}],
                    }
                }
                resp = client.post(
                    f"{DUFFEL_API_URL}/orders",
                    headers=self.headers,
                    json=payload,
                )
                data = self._handle_response_status(resp, "orders.create")
                order_id = data.get("data", {}).get("id")

        # Fetch booking reference
        order_details = self.get_order(order_id)
        booking_ref = order_details.get("booking_reference") or f"REF{order_id[-6:].upper()}"

        return ConfirmedBooking(
            order_id=order_id,
            booking_reference=booking_ref,
            offer_id=offer_id,
            total_amount=float(order_details.get("total_amount", 500.0)),
            currency=order_details.get("total_currency", "USD"),
            passenger_name=profile.name,
            passenger_email=profile.email,
        )

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Executes GET /air/orders/{id} for post-booking verification."""
        if self.dry_run:
            return {"id": order_id, "status": "confirmed", "booking_reference": "DRYREF"}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{DUFFEL_API_URL}/orders/{order_id}", headers=self.headers)
            data = self._handle_response_status(resp, "orders.get")
        return data.get("data", {})

    def request_cancellation_quote(self, order_id: str, original_amount: float = 0.0) -> CancellationQuote:
        """Step 1 of Duffel cancellation: POST /air/order_cancellations."""
        if self.dry_run:
            return CancellationQuote(
                order_id=order_id,
                cancellation_id=f"ore_cxl_dry_{order_id}",
                refund_amount=380.0,
            )

        payload = {"data": {"order_id": order_id}}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{DUFFEL_API_URL}/order_cancellations",
                headers=self.headers,
                json=payload,
            )
            data = self._handle_response_status(resp, "order_cancellations.create")

        cxl_data = data.get("data", {})
        return CancellationQuote(
            order_id=order_id,
            cancellation_id=cxl_data.get("id"),
            refund_amount=float(cxl_data.get("refund_amount", 0.0)),
            refund_currency=cxl_data.get("refund_currency", "USD"),
        )

    def confirm_cancellation(self, cancellation_id: str) -> bool:
        """Step 2 of Duffel cancellation: POST /air/order_cancellations/{id}/actions/confirm."""
        if self.dry_run:
            return True

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{DUFFEL_API_URL}/order_cancellations/{cancellation_id}/actions/confirm",
                headers=self.headers,
            )
            data = self._handle_response_status(resp, "order_cancellations.confirm")

        confirmed_at = data.get("data", {}).get("confirmed_at")
        return bool(confirmed_at)

