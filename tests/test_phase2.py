"""
Unit Tests for Phase 2: Dual-Mode Clients and Recording Fakes with Fault Injection.
Run via: python3 -m unittest tests/test_phase2.py
"""

import unittest
from datetime import datetime, timedelta

from agent.models import CabinClass, FlightOffer, TravelerProfile
from clients.base import NonRetryableAPIError, RetryableAPIError, measure_latency
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio


class TestPhase2(unittest.TestCase):
    def setUp(self):
        with open("profiles/alex.json", "r") as f:
            self.profile = TravelerProfile.model_validate_json(f.read())

        self.sample_offer = FlightOffer(
            id="off_test_1",
            carrier="ZZ",
            flight_number="ZZ101",
            departs_at=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            total_amount=500.00,
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

    # -------------------------------------------------------------------------
    # 1. Base Utilities & Latency Profiling
    # -------------------------------------------------------------------------
    def test_latency_measurement(self):
        with measure_latency() as lat:
            total = sum(range(100000))
        self.assertGreaterEqual(lat["ms"], 0)

    # -------------------------------------------------------------------------
    # 2. FakeDuffel Lifecycle & Fault Injection
    # -------------------------------------------------------------------------
    def test_fake_duffel_standard_lifecycle(self):
        duffel = FakeDuffel(offers=[self.sample_offer])

        # 1. Search
        offers = duffel.search_offers("LHR", "JFK", "2026-09-14")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].id, "off_test_1")

        # 2. Hold Order
        hold = duffel.create_hold_order("off_test_1", self.profile)
        self.assertEqual(hold.status, "hold")
        self.assertEqual(hold.total_amount, 500.00)

        # 3. Confirm Booking
        booking = duffel.confirm_booking("off_test_1", self.profile, hold_order_id=hold.order_id)
        self.assertIn("REF", booking.booking_reference)
        self.assertEqual(booking.passenger_name, self.profile.name)

        # 4. Verify step (GET order)
        order_details = duffel.get_order(booking.order_id)
        self.assertEqual(order_details["status"], "confirmed")

        # 5. Cancellation sequence
        quote = duffel.request_cancellation_quote("ord_original_123", original_amount=380.0)
        self.assertEqual(quote.refund_amount, 380.0)
        confirmed = duffel.confirm_cancellation(quote.cancellation_id)
        self.assertTrue(confirmed)
        self.assertEqual(duffel.orders["ord_original_123"]["status"], "cancelled")

    def test_fake_duffel_injected_retries_then_success(self):
        # Scenario S11 pattern: 500 twice, then ok
        failures = [
            {"call": "offer_requests.create", "sequence": ["500", "500", "ok"]}
        ]
        duffel = FakeDuffel(offers=[self.sample_offer], failures=failures)

        # First call fails with 500
        with self.assertRaises(RetryableAPIError):
            duffel.search_offers("LHR", "JFK", "2026-09-14")

        # Second call fails with 500
        with self.assertRaises(RetryableAPIError):
            duffel.search_offers("LHR", "JFK", "2026-09-14")

        # Third call succeeds
        offers = duffel.search_offers("LHR", "JFK", "2026-09-14")
        self.assertEqual(len(offers), 1)
        self.assertEqual(duffel.retry_counts.get("offer_requests.create"), 2)

    def test_fake_duffel_offer_expired_injection(self):
        # Scenario S13 pattern
        failures = [{"trigger": "offer_expired"}]
        duffel = FakeDuffel(offers=[self.sample_offer], failures=failures)

        with self.assertRaises(NonRetryableAPIError) as ctx:
            duffel.confirm_booking("off_test_1", self.profile)
        self.assertIn("offer_expired", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 3. FakeCalendar Lifecycle & Fault Injection
    # -------------------------------------------------------------------------
    def test_fake_calendar_operations(self):
        target_deadline = datetime.fromisoformat("2026-09-15T09:00:00Z")
        calendar = FakeCalendar(deadline=target_deadline)

        # Read deadline
        deadline = calendar.get_destination_deadline(datetime.fromisoformat("2026-09-14T13:00:00Z"))
        self.assertEqual(deadline, target_deadline)

        # Patch flight event
        patched = calendar.patch_trip_event(
            event_id="evt_cal_123",
            new_start=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            new_end=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            booking_ref="REF999",
        )
        self.assertEqual(patched["booking_reference"], "REF999")

        # Test 503 fault injection
        failing_cal = FakeCalendar(injected_failure="503")
        with self.assertRaises(RetryableAPIError):
            failing_cal.get_destination_deadline(datetime.now())

    # -------------------------------------------------------------------------
    # 4. FakeGmail & FakeTwilio Operations
    # -------------------------------------------------------------------------
    def test_fake_gmail_and_twilio(self):
        gmail = FakeGmail()
        sent = gmail.send_itinerary_email("alex@example.com", "Rebooked: ZZ201", "Itinerary body")
        self.assertEqual(len(gmail.sent_emails), 1)

        draft = gmail.create_compensation_draft("claims@airline.com", "EU261 Claim", "Claim body")
        self.assertEqual(len(gmail.drafts), 1)

        twilio = FakeTwilio()
        sms = twilio.send_sms("+15550000001", "Reply 1 to book", sms_type="approval_request")
        self.assertEqual(len(twilio.sent_messages), 1)
        self.assertEqual(sms["type"], "approval_request")

        # Test Twilio 500 failure injection
        failing_twilio = FakeTwilio(injected_failure="500")
        with self.assertRaises(RetryableAPIError):
            failing_twilio.send_sms("+15550000001", "Test")


if __name__ == "__main__":
    unittest.main()

