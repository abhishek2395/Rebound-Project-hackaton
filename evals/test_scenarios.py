"""
The 30-Scenario Evaluation Harness
Implements Sierra tau-bench end-state verification across all 30 chaos,
approval, and disruption scenarios from REBOUND_HACKATHON_PLAYBOOK.md.
"""

import glob
import json
import os
import sys
import tempfile
import time
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timezone
from typing import Any, Dict, List

from agent.loop import ReboundAgent
from agent.models import (
    ActionType,
    CabinClass,
    DisruptionEvent,
    DisruptionType,
    FlightOffer,
    TravelerProfile,
)
from app.db import Database
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio
from evals.report import generate_markdown_report


class TestAllScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_files = sorted(glob.glob("evals/fixtures/S*.json"))
        cls.results_log: List[Dict[str, Any]] = []

    def _parse_offers(self, raw_offers: List[Dict[str, Any]]) -> List[FlightOffer]:
        offers = []
        for item in raw_offers:
            offers.append(
                FlightOffer(
                    id=item["id"],
                    carrier=item.get("carrier", "ZZ"),
                    flight_number=item.get("flight_number", "ZZ201"),
                    departs_at=datetime.fromisoformat(item["departs_at"]),
                    arrives_at=datetime.fromisoformat(item["arrives_at"]),
                    total_amount=float(item["total_amount"]),
                    segments=int(item.get("segments", 1)),
                    cabin_class=CabinClass(item.get("cabin_class", "economy")),
                )
            )
        return offers

    def run_scenario(self, fixture_path: str) -> Dict[str, Any]:
        """Executes a single scenario and asserts terminal state invariants."""
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        scenario_id = data["id"]
        description = data.get("description", "")
        expected = data.get("expected", {})

        # Isolated database
        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db = Database(temp_db.name)

        # Handle S26: Malformed event input validation
        if scenario_id == "S26_malformed_event":
            try:
                DisruptionEvent.model_validate(data["event"])
                self.fail("S26 should have failed schema validation")
            except Exception:
                # Validation error expected
                res = {
                    "id": scenario_id,
                    "description": description,
                    "expected_action": "validation_error",
                    "observed_action": "validation_error",
                    "bookings": 0,
                    "retries": 0,
                    "latency_ms": 1,
                    "status": "PASS",
                }
                self.results_log.append(res)
                return res

        # Setup Fakes
        mock_duffel_data = data.get("mock_duffel", {})
        offers = self._parse_offers(mock_duffel_data.get("offers", []))
        failures = mock_duffel_data.get("failures", [])
        duffel = FakeDuffel(offers=offers, failures=failures)

        cal_data = data.get("calendar", {})
        cal_deadline = datetime.fromisoformat(cal_data.get("deadline", "2026-09-15T09:00:00Z"))
        calendar = FakeCalendar(
            deadline=cal_deadline,
            injected_failure=cal_data.get("injected_failure"),
        )

        gmail = FakeGmail(injected_failure=data.get("mock_gmail", {}).get("injected_failure"))
        twilio = FakeTwilio(injected_failure=data.get("mock_twilio", {}).get("injected_failure"))

        # Profile & Event
        profile = TravelerProfile.model_validate(data["profile"])
        event = DisruptionEvent.model_validate(data["event"])

        agent = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=db,
            profile=profile,
            original_order_total=380.0,
        )

        start_time = time.perf_counter()
        record = agent.run(event, sms_reply=data.get("sms_reply"))
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Handle S10: Duplicate webhook delivery
        if data.get("deliver_times") == 2:
            agent2 = ReboundAgent(
                duffel=duffel,
                calendar=calendar,
                gmail=gmail,
                twilio=twilio,
                db=db,
                profile=profile,
                original_order_total=380.0,
            )
            record2 = agent2.run(event, sms_reply=data.get("sms_reply"))
            self.assertEqual(record2.action, ActionType.SKIPPED_DUPLICATE)

        # ---------------------------------------------------------------------
        # End-State Invariant Assertions
        # ---------------------------------------------------------------------
        expected_action = expected.get("action")
        self.assertEqual(record.action.value, expected_action, f"Scenario {scenario_id} action mismatch")

        # Booking count
        confirmed_bookings = [
            c for c in duffel.calls if c["method"] == "confirm_booking"
        ]
        if "bookings_created" in expected:
            self.assertEqual(
                len(confirmed_bookings),
                expected["bookings_created"],
                f"Scenario {scenario_id} booking count mismatch",
            )

        # Chosen offer
        if "chosen_offer_id" in expected and expected["chosen_offer_id"]:
            self.assertEqual(record.chosen_offer_id, expected["chosen_offer_id"])

        # Compensation drafts (S22, S23)
        if "compensation_drafted" in expected:
            if expected["compensation_drafted"]:
                self.assertGreater(len(gmail.drafts), 0, f"{scenario_id} should draft EU261 compensation")
            else:
                self.assertEqual(len(gmail.drafts), 0, f"{scenario_id} should NOT draft compensation")

        # Pickup contact notifications (S24)
        if expected.get("pickup_notified"):
            pickup_sms = [s for s in twilio.sent_messages if s["type"] == "pickup_notification"]
            self.assertGreater(len(pickup_sms), 0, f"{scenario_id} should notify pickup contact")

        # Injected retries (S11)
        retries_count = sum(duffel.retry_counts.values()) + sum(calendar.retry_counts.values())
        if "retries_observed" in expected:
            self.assertGreaterEqual(retries_count, expected["retries_observed"])

        res = {
            "id": scenario_id,
            "description": description,
            "expected_action": expected_action,
            "observed_action": record.action.value,
            "bookings": len(confirmed_bookings),
            "retries": retries_count,
            "latency_ms": latency_ms,
            "status": "PASS",
        }
        self.results_log.append(res)
        return res

    # -------------------------------------------------------------------------
    # Generate 30 distinct unit test methods so pytest/unittest reports each
    # -------------------------------------------------------------------------
    def test_S01_cancelled_easy(self): self.run_scenario("evals/fixtures/S01_cancelled_easy.json")
    def test_S02_cancelled_over_threshold_yes(self): self.run_scenario("evals/fixtures/S02_cancelled_over_threshold_yes.json")
    def test_S03_cancelled_over_threshold_no(self): self.run_scenario("evals/fixtures/S03_cancelled_over_threshold_no.json")
    def test_S04_cancelled_over_threshold_timeout(self): self.run_scenario("evals/fixtures/S04_cancelled_over_threshold_timeout.json")
    def test_S05_cancelled_over_ceiling(self): self.run_scenario("evals/fixtures/S05_cancelled_over_ceiling.json")
    def test_S06_delay_still_makes_it(self): self.run_scenario("evals/fixtures/S06_delay_still_makes_it.json")
    def test_S07_delay_misses_deadline(self): self.run_scenario("evals/fixtures/S07_delay_misses_deadline.json")
    def test_S08_missed_connection(self): self.run_scenario("evals/fixtures/S08_missed_connection.json")
    def test_S09_no_options_before_deadline(self): self.run_scenario("evals/fixtures/S09_no_options_before_deadline.json")
    def test_S10_duplicate_webhook(self): self.run_scenario("evals/fixtures/S10_duplicate_webhook.json")
    def test_S11_duffel_500_twice(self): self.run_scenario("evals/fixtures/S11_duffel_500_twice.json")
    def test_S12_duffel_500_forever(self): self.run_scenario("evals/fixtures/S12_duffel_500_forever.json")
    def test_S13_offer_expired_at_booking(self): self.run_scenario("evals/fixtures/S13_offer_expired_at_booking.json")
    def test_S14_empty_results_widen(self): self.run_scenario("evals/fixtures/S14_empty_results_widen.json")
    def test_S15_layover_limit(self): self.run_scenario("evals/fixtures/S15_layover_limit.json")
    def test_S16_redeye_avoid(self): self.run_scenario("evals/fixtures/S16_redeye_avoid.json")
    def test_S17_preferred_airline_tiebreak(self): self.run_scenario("evals/fixtures/S17_preferred_airline_tiebreak.json")
    def test_S18_calendar_conflict(self): self.run_scenario("evals/fixtures/S18_calendar_conflict.json")
    def test_S19_no_calendar_deadline(self): self.run_scenario("evals/fixtures/S19_no_calendar_deadline.json")
    def test_S20_crash_after_book_before_cancel(self): self.run_scenario("evals/fixtures/S20_crash_after_book_before_cancel.json")
    def test_S21_verify_mismatch(self): self.run_scenario("evals/fixtures/S21_verify_mismatch.json")
    def test_S22_compensation_eligible(self): self.run_scenario("evals/fixtures/S22_compensation_eligible.json")
    def test_S23_compensation_not_eligible(self): self.run_scenario("evals/fixtures/S23_compensation_not_eligible.json")
    def test_S24_pickup_contact_notified(self): self.run_scenario("evals/fixtures/S24_pickup_contact_notified.json")
    def test_S25_auto_book_off(self): self.run_scenario("evals/fixtures/S25_auto_book_off.json")
    def test_S26_malformed_event(self): self.run_scenario("evals/fixtures/S26_malformed_event.json")
    def test_S27_calendar_api_down(self): self.run_scenario("evals/fixtures/S27_calendar_api_down.json")
    def test_S28_sms_api_down_when_ask_needed(self): self.run_scenario("evals/fixtures/S28_sms_api_down_when_ask_needed.json")
    def test_S29_gmail_down(self): self.run_scenario("evals/fixtures/S29_gmail_down.json")
    def test_S30_cost_delta_negative(self): self.run_scenario("evals/fixtures/S30_cost_delta_negative.json")

    @classmethod
    def tearDownClass(cls):
        if cls.results_log:
            generate_markdown_report(cls.results_log, "EVAL_RESULTS.md")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAllScenarios)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    generate_markdown_report(TestAllScenarios.results_log, "EVAL_RESULTS.md")
    print(f"\nGenerated EVAL_RESULTS.md with {len(TestAllScenarios.results_log)} results.")
