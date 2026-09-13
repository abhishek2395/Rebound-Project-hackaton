"""
Duffel sandbox sanity check.

Runs one end-to-end flow against the real Duffel test API so we know our key
works before wiring the agent to it:

    offer_request -> pick cheapest ZZ offer -> create order -> GET order -> cancel

Requires DUFFEL_API_KEY in the environment (must start with `duffel_test_`).

Usage:
    python scripts/manual_booking.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

API_URL = "https://api.duffel.com/air"
API_KEY = os.getenv("DUFFEL_API_KEY", "")
# NOTE: If Duffel rejects with 400 "unsupported version", swap this for the
# dated version their error message names (e.g. "2024-04-01").
DUFFEL_VERSION = os.getenv("DUFFEL_VERSION", "v2")


def die(msg: str, response: httpx.Response | None = None) -> None:
    print(f"\n❌ {msg}")
    if response is not None:
        print(f"   status={response.status_code}")
        try:
            print(f"   body={json.dumps(response.json(), indent=2)[:800]}")
        except Exception:
            print(f"   body={response.text[:800]}")
    sys.exit(1)


def headers() -> dict:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Duffel-Version": DUFFEL_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def step(n: int, title: str) -> None:
    print(f"\n── Step {n}: {title} " + "─" * (60 - len(title)))


def main() -> None:
    if not API_KEY:
        die("DUFFEL_API_KEY is not set. Put it in .env and re-run.")
    if not API_KEY.startswith("duffel_test_"):
        die("Refusing to run: key does not start with 'duffel_test_'. Use a test key.")

    print(f"Using key: {API_KEY[:14]}...{API_KEY[-4:]}")
    depart = (datetime.now(timezone.utc) + timedelta(days=14)).strftime("%Y-%m-%d")

    with httpx.Client(timeout=60.0, headers=headers()) as client:
        # 1. Offer request
        step(1, "POST /air/offer_requests (SFO -> JFK)")
        payload = {
            "data": {
                "slices": [
                    {"origin": "SFO", "destination": "JFK", "departure_date": depart}
                ],
                "passengers": [{"type": "adult"}],
                "cabin_class": "economy",
            }
        }
        r = client.post(f"{API_URL}/offer_requests?return_offers=true", json=payload)
        if r.status_code >= 300:
            die("offer_requests failed", r)
        offer_request = r.json()["data"]
        offers = offer_request.get("offers", [])
        print(f"   ✓ {len(offers)} offers returned")
        if not offers:
            die("No offers returned — cannot continue.")

        zz_offers = [o for o in offers if o["owner"]["iata_code"] == "ZZ"] or offers
        offer = sorted(zz_offers, key=lambda o: float(o["total_amount"]))[0]
        offer_id = offer["id"]
        total = offer["total_amount"]
        curr = offer["total_currency"]
        print(f"   ✓ Cheapest offer: {offer_id} @ {total} {curr}")

        # 2. Create order
        step(2, "POST /air/orders")
        passenger_ids = [p["id"] for p in offer["passengers"]]
        order_payload = {
            "data": {
                "type": "instant",
                "selected_offers": [offer_id],
                "passengers": [
                    {
                        "id": pid,
                        "title": "mr",
                        "given_name": "Alex",
                        "family_name": "Rivera",
                        "born_on": "1990-01-01",
                        "gender": "m",
                        "email": "alex@example.com",
                        "phone_number": "+14158675309",
                    }
                    for pid in passenger_ids
                ],
                "payments": [
                    {"type": "balance", "amount": total, "currency": curr}
                ],
            }
        }
        r = client.post(f"{API_URL}/orders", json=order_payload)
        if r.status_code >= 300:
            die("orders create failed", r)
        order = r.json()["data"]
        order_id = order["id"]
        print(f"   ✓ Order created: {order_id}  booking_ref={order.get('booking_reference')}")

        # 3. Verify via GET
        step(3, f"GET /air/orders/{order_id}")
        r = client.get(f"{API_URL}/orders/{order_id}")
        if r.status_code >= 300:
            die("orders get failed", r)
        verified = r.json()["data"]
        print(f"   ✓ Verified. status={verified.get('booking_reference')}, passengers={len(verified.get('passengers', []))}")

        # 4. Cancel
        step(4, "POST /air/order_cancellations")
        r = client.post(
            f"{API_URL}/order_cancellations", json={"data": {"order_id": order_id}}
        )
        if r.status_code >= 300:
            die("order_cancellations create failed", r)
        cx = r.json()["data"]
        cx_id = cx["id"]
        print(f"   ✓ Cancellation quoted: {cx_id}, refund={cx.get('refund_amount')} {cx.get('refund_currency')}")

        step(5, f"POST /air/order_cancellations/{cx_id}/actions/confirm")
        r = client.post(f"{API_URL}/order_cancellations/{cx_id}/actions/confirm")
        if r.status_code >= 300:
            die("cancellation confirm failed", r)
        print("   ✓ Cancellation confirmed.")

    print("\n✅ Sandbox sanity check passed. Duffel key is good.")
    print(f"   Order {order_id} was created and cancelled in your test account.")


if __name__ == "__main__":
    main()
