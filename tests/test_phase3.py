"""
Unit Tests for Phase 3: Rebound Agent Loop, Idempotency, and FastAPI Webhooks.
Run via: python3 -m unittest tests/test_phase3.py
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from agent.loop import ReboundAgent
from agent.models import (
    ActionType,
    CabinClass,
    DisruptionEvent,
    DisruptionType,
    FlightDetails,
    FlightOffer,
    TravelerProfile,
)
from app.db import Database
from app.main import app
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio


class TestPhase3(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary database for each test
        self.temp_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self.temp_db_file.name)

        with open("profiles/alex.json", "r") as f:
            self.profile = TravelerProfile.model_validate_json(f.read())

        self.flight = FlightDetails(
            carrier="ZZ",
            number="ZZ123",
            origin="LHR",
            destination="JFK",
            scheduled_departure=datetime.fromisoformat("2026-09-14T10:00:00Z"),
            scheduled_arrival=datetime.fromisoformat("2026-09-14T13:00:00Z"),
        )

        self.event_s01 = DisruptionEvent(
            event_id="evt_test_s01",
            type=DisruptionType.CANCELLED,
            order_id="ord_orig_100",
            flight=self.flight,
        )

        # Standard offers
        self.offer_a = FlightOffer(
            id="off_a",
            carrier="ZZ",
            flight_number="ZZ201",
            departs_at=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            total_amount=500.00,  # Delta = +$120.00 vs $380
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

        self.offer_over_threshold = FlightOffer(
            id="off_over",
            carrier="ZZ",
            flight_number="ZZ202",
            departs_at=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            total_amount=830.00,  # Delta = +$450.00 vs $380
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

        self.target_deadline = datetime.fromisoformat("2026-09-15T09:00:00Z")

    def tearDown(self):
        try:
            os.remove(self.temp_db_file.name)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Scenario S01: Easy Cancellation (Auto-Book within Threshold)
    # -------------------------------------------------------------------------
    def test_scenario_s01_easy_rebook(self):
        duffel = FakeDuffel(offers=[self.offer_a])
        calendar = FakeCalendar(deadline=self.target_deadline)
        gmail = FakeGmail()
        twilio = FakeTwilio()

        agent = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=self.db,
            profile=self.profile,
            original_order_total=380.0,
        )

        record = agent.run(self.event_s01)

        # Verify decision
        self.assertEqual(record.action, ActionType.BOOK)
        self.assertEqual(record.chosen_offer_id, "off_a")
        self.assertEqual(record.cost_delta_usd, 120.00)

        # Verify irreversible action sequencing
        self.assertEqual(len(duffel.orders), 2)  # 1 new confirmed order + 1 cancelled old order
        self.assertTrue(duffel.orders["ord_orig_100"]["status"] == "cancelled")
        self.assertEqual(len(calendar.events), 1, "Calendar event should be patched")
        self.assertEqual(len(gmail.sent_emails), 1, "Itinerary email should be sent")
        self.assertEqual(len(twilio.sent_messages), 2, "Pickup contact and summary SMS should be dispatched")

    # -------------------------------------------------------------------------
    # Scenario S02: Over Threshold with Approval Reply "1"
    # -------------------------------------------------------------------------
    def test_scenario_s02_approval_yes(self):
        duffel = FakeDuffel(offers=[self.offer_over_threshold])
        calendar = FakeCalendar(deadline=self.target_deadline)
        gmail = FakeGmail()
        twilio = FakeTwilio()

        event = DisruptionEvent(
            event_id="evt_test_s02",
            type=DisruptionType.CANCELLED,
            order_id="ord_orig_100",
            flight=self.flight,
        )

        agent = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=self.db,
            profile=self.profile,
            original_order_total=380.0,
        )

        # Run with simulated instant reply "1"
        record = agent.run(event, sms_reply="1")

        self.assertEqual(record.action, ActionType.ASK_THEN_BOOK)
        self.assertEqual(record.chosen_offer_id, "off_over")
        self.assertEqual(record.cost_delta_usd, 450.00)

        # Verify that a hold order was created before the booking was confirmed
        # (Hold-Order Pattern: lock price/inventory, then convert the SAME hold
        # into the paid booking once the traveler approves via SMS -- it must
        # not be left dangling in "hold" status once approval completes).
        call_methods = [c["method"] for c in duffel.calls]
        self.assertIn("create_hold_order", call_methods, "Hold order must be created before SMS approval")
        self.assertIn("confirm_booking", call_methods)
        self.assertLess(
            call_methods.index("create_hold_order"),
            call_methods.index("confirm_booking"),
            "Hold order must lock price before SMS approval",
        )
        hold_orders = [o for o in duffel.orders.values() if o.get("status") == "hold"]
        self.assertEqual(len(hold_orders), 0, "Hold order must be converted into the confirmed booking, not abandoned")
        confirmed_orders = [oid for oid, o in duffel.orders.items() if o.get("status") == "confirmed"]
        self.assertEqual(len(confirmed_orders), 1)
        self.assertTrue(
            confirmed_orders[0].startswith("ord_hold_"),
            "Confirmed booking must reuse the original hold order id, not mint a new one",
        )
        self.assertEqual(len(gmail.sent_emails), 1)

    # -------------------------------------------------------------------------
    # Scenario S03: Over Threshold with Rejection Reply "NO"
    # -------------------------------------------------------------------------
    def test_scenario_s03_approval_no(self):
        duffel = FakeDuffel(offers=[self.offer_over_threshold])
        calendar = FakeCalendar(deadline=self.target_deadline)
        gmail = FakeGmail()
        twilio = FakeTwilio()

        event = DisruptionEvent(
            event_id="evt_test_s03",
            type=DisruptionType.CANCELLED,
            order_id="ord_orig_100",
            flight=self.flight,
        )

        agent = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=self.db,
            profile=self.profile,
            original_order_total=380.0,
        )

        record = agent.run(event, sms_reply="NO")

        self.assertEqual(record.action, ActionType.ESCALATE)
        self.assertIsNone(record.chosen_offer_id)
        # Old order must NOT be cancelled
        self.assertNotIn("ord_orig_100", duffel.orders)
        self.assertEqual(len(gmail.sent_emails), 0, "No itinerary should be emailed on rejection")

    # -------------------------------------------------------------------------
    # Scenario S10: Idempotency & Duplicate Webhooks
    # -------------------------------------------------------------------------
    def test_scenario_s10_idempotency_duplicate_event(self):
        duffel = FakeDuffel(offers=[self.offer_a])
        calendar = FakeCalendar(deadline=self.target_deadline)
        gmail = FakeGmail()
        twilio = FakeTwilio()

        agent1 = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=self.db,
            profile=self.profile,
        )
        record1 = agent1.run(self.event_s01)
        self.assertEqual(record1.action, ActionType.BOOK)

        # Deliver the exact same event a second time
        agent2 = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=self.db,
            profile=self.profile,
        )
        record2 = agent2.run(self.event_s01)
        self.assertEqual(record2.action, ActionType.SKIPPED_DUPLICATE)

        # Assert no second booking was created
        confirmed_bookings = [
            call for call in duffel.calls if call["method"] == "confirm_booking"
        ]
        self.assertEqual(len(confirmed_bookings), 1, "Duplicate event must not create a 2nd booking")

    # -------------------------------------------------------------------------
    # FastAPI Webhook Ingress Tests
    # -------------------------------------------------------------------------
    def test_fastapi_endpoints(self):
        client = TestClient(app)

        # Health check
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "healthy")

        # Ingest disruption webhook
        unique_event_id = f"evt_api_test_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        payload = {
            "event_id": unique_event_id,
            "type": "cancelled",
            "order_id": "ord_api_test",
            "flight": {
                "carrier": "ZZ",
                "number": "ZZ555",
                "origin": "LHR",
                "destination": "JFK",
                "scheduled_departure": "2026-09-14T10:00:00Z",
                "scheduled_arrival": "2026-09-14T13:00:00Z",
            },
        }
        res_post = client.post("/webhook/disruption", json=payload)
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(res_post.json()["status"], "accepted")

        # Second delivery of same payload should return skipped_duplicate
        res_duplicate = client.post("/webhook/disruption", json=payload)
        self.assertEqual(res_duplicate.status_code, 200)
        self.assertEqual(res_duplicate.json()["status"], "skipped_duplicate")

        # Demo trigger endpoint
        res_demo = client.post("/api/demo/trigger?scenario=S01")
        self.assertEqual(res_demo.status_code, 200)
        demo_data = res_demo.json()
        self.assertIn("run_id", demo_data)
        run_id = demo_data["run_id"]

        # List runs endpoint
        res_runs = client.get("/api/runs")
        self.assertEqual(res_runs.status_code, 200)
        self.assertIn("runs", res_runs.json())

        # Trace endpoint
        res_trace = client.get(f"/api/traces/{run_id}")
        self.assertEqual(res_trace.status_code, 200)
        self.assertIn("entries", res_trace.json())

        # Viewer static endpoint
        res_viewer = client.get("/viewer/")
        self.assertEqual(res_viewer.status_code, 200)
        self.assertIn(b"REBOUND", res_viewer.content)


if __name__ == "__main__":
    unittest.main()
