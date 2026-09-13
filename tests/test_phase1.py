"""
Unit Tests for Phase 1: Core Models, Policy Guardrails, and Prompt Parsers.
Run via: python3 -m unittest tests/test_phase1.py (or pytest tests/test_phase1.py)
"""

import json
import unittest
from datetime import datetime

from agent.models import (
    ActionType,
    CabinClass,
    DisruptionEvent,
    DisruptionType,
    FlightDetails,
    FlightOffer,
    TravelerProfile,
)
from agent.policy import (
    check_eu261_eligibility,
    evaluate_mandate,
    filter_survivors,
    is_delay_acceptable,
)
from agent.prompts import (
    format_ranking_user_prompt,
    parse_ranking_response,
)


class TestPhase1(unittest.TestCase):
    def setUp(self):
        with open("profiles/alex.json", "r") as f:
            self.profile = TravelerProfile.model_validate_json(f.read())

        self.original_order_total = 380.0
        self.deadline = datetime.fromisoformat("2026-09-15T09:00:00Z")

        # Standard sample offers
        self.offer_easy = FlightOffer(
            id="off_easy",
            carrier="ZZ",
            flight_number="ZZ201",
            departs_at=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            total_amount=500.00,  # Delta = +$120.00 (within $300 threshold)
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

        self.offer_over_threshold = FlightOffer(
            id="off_high",
            carrier="ZZ",
            flight_number="ZZ202",
            departs_at=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            total_amount=830.00,  # Delta = +$450.00 (between $300 and $800)
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

        self.offer_over_ceiling = FlightOffer(
            id="off_expensive",
            carrier="ZZ",
            flight_number="ZZ999",
            departs_at=datetime.fromisoformat("2026-09-14T14:10:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T17:05:00Z"),
            total_amount=1580.00,  # Delta = +$1200.00 (> $800 ceiling)
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

        self.offer_too_many_layovers = FlightOffer(
            id="off_layovers",
            carrier="ZZ",
            flight_number="ZZ301",
            departs_at=datetime.fromisoformat("2026-09-14T12:00:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-14T20:00:00Z"),
            total_amount=400.00,
            segments=3,  # 2 layovers (exceeds max_layovers=1)
            cabin_class=CabinClass.ECONOMY,
        )

        self.offer_after_deadline = FlightOffer(
            id="off_late",
            carrier="ZZ",
            flight_number="ZZ401",
            departs_at=datetime.fromisoformat("2026-09-15T06:00:00Z"),
            arrives_at=datetime.fromisoformat("2026-09-15T09:30:00Z"),  # After 08:00 (09:00 - 60m buffer)
            total_amount=420.00,
            segments=1,
            cabin_class=CabinClass.ECONOMY,
        )

    # -------------------------------------------------------------------------
    # 1. Model Validation Tests
    # -------------------------------------------------------------------------
    def test_disruption_event_validation(self):
        event = DisruptionEvent(
            event_id="evt_001",
            type=DisruptionType.CANCELLED,
            order_id="ord_test",
            flight=FlightDetails(
                carrier="ZZ",
                number="ZZ123",
                origin="LHR",
                destination="JFK",
                scheduled_departure=datetime.fromisoformat("2026-09-14T10:00:00Z"),
                scheduled_arrival=datetime.fromisoformat("2026-09-14T13:00:00Z"),
            ),
        )
        self.assertEqual(event.event_id, "evt_001")
        self.assertEqual(event.type, DisruptionType.CANCELLED)

        # Empty event_id should raise ValueError
        with self.assertRaises(ValueError):
            DisruptionEvent(
                event_id="",
                type=DisruptionType.CANCELLED,
                flight=event.flight,
            )

    # -------------------------------------------------------------------------
    # 2. Policy Guardrails: filter_survivors
    # -------------------------------------------------------------------------
    def test_filter_survivors_discards_late_and_excess_layovers(self):
        all_offers = [
            self.offer_easy,
            self.offer_after_deadline,
            self.offer_too_many_layovers,
        ]
        survivors = filter_survivors(all_offers, self.profile, self.deadline)
        survivor_ids = [s.id for s in survivors]

        self.assertIn("off_easy", survivor_ids)
        self.assertNotIn("off_late", survivor_ids, "Should discard flight arriving after deadline - buffer")
        self.assertNotIn("off_layovers", survivor_ids, "Should discard flight with > 1 layovers")

    # -------------------------------------------------------------------------
    # 3. Policy Guardrails: is_delay_acceptable
    # -------------------------------------------------------------------------
    def test_is_delay_acceptable(self):
        event_acceptable = DisruptionEvent(
            event_id="evt_del1",
            type=DisruptionType.DELAYED,
            delay_minutes=60,  # 1 hour delay on arrival 13:00 -> 14:00 (deadline is tomorrow 09:00)
            flight=FlightDetails(
                carrier="ZZ",
                number="ZZ123",
                origin="LHR",
                destination="JFK",
                scheduled_departure=datetime.fromisoformat("2026-09-14T10:00:00Z"),
                scheduled_arrival=datetime.fromisoformat("2026-09-14T13:00:00Z"),
            ),
        )
        self.assertTrue(is_delay_acceptable(event_acceptable, self.deadline))

        # Delay that pushes arrival past deadline - 60min buffer (deadline 15:00, arrival was 13:00, delay 150m -> 15:30)
        tight_deadline = datetime.fromisoformat("2026-09-14T15:00:00Z")
        event_unacceptable = DisruptionEvent(
            event_id="evt_del2",
            type=DisruptionType.DELAYED,
            delay_minutes=150,
            flight=event_acceptable.flight,
        )
        self.assertFalse(is_delay_acceptable(event_unacceptable, tight_deadline))

    # -------------------------------------------------------------------------
    # 4. Mandate Evaluation & Routing
    # -------------------------------------------------------------------------
    def test_evaluate_mandate_auto_book(self):
        # Delta = +$120.00 <= $300 approval threshold -> BOOK
        action, delta, reason = evaluate_mandate(self.offer_easy, self.profile, self.original_order_total)
        self.assertEqual(action, ActionType.BOOK)
        self.assertEqual(delta, 120.00)

    def test_evaluate_mandate_ask_approval(self):
        # Delta = +$450.00 ($300 < delta <= $800) -> ASK
        action, delta, reason = evaluate_mandate(self.offer_over_threshold, self.profile, self.original_order_total)
        self.assertEqual(action, ActionType.ASK)
        self.assertEqual(delta, 450.00)

    def test_evaluate_mandate_escalate_over_ceiling(self):
        # Delta = +$1200.00 > $800 ceiling -> ESCALATE
        action, delta, reason = evaluate_mandate(self.offer_over_ceiling, self.profile, self.original_order_total)
        self.assertEqual(action, ActionType.ESCALATE)
        self.assertEqual(delta, 1200.00)

    def test_evaluate_mandate_auto_book_disabled(self):
        profile_no_auto = self.profile.model_copy(deep=True)
        profile_no_auto.mandate.auto_book = False

        # Even with delta <= $300, auto_book=False requires ASK
        action, delta, reason = evaluate_mandate(self.offer_easy, profile_no_auto, self.original_order_total)
        self.assertEqual(action, ActionType.ASK)

    def test_evaluate_mandate_negative_delta_refund(self):
        cheaper_offer = self.offer_easy.model_copy()
        cheaper_offer.total_amount = 300.00  # Delta = -$80.00
        action, delta, reason = evaluate_mandate(cheaper_offer, self.profile, self.original_order_total)
        self.assertEqual(action, ActionType.BOOK)
        self.assertEqual(delta, -80.00)

    # -------------------------------------------------------------------------
    # 5. EU261 Compensation Eligibility
    # -------------------------------------------------------------------------
    def test_eu261_eligibility(self):
        event_lhr_operational = DisruptionEvent(
            event_id="evt_eu1",
            type=DisruptionType.CANCELLED,
            reason="operational crew shortage",
            flight=FlightDetails(
                carrier="ZZ",
                number="ZZ123",
                origin="LHR",  # EU/UK origin
                destination="JFK",
                scheduled_departure=datetime.fromisoformat("2026-09-14T10:00:00Z"),
                scheduled_arrival=datetime.fromisoformat("2026-09-14T13:00:00Z"),
            ),
        )
        eligible, reason = check_eu261_eligibility(event_lhr_operational, notice_days=1)
        self.assertTrue(eligible)

        # Weather cancellation is exempt (extraordinary)
        event_weather = event_lhr_operational.model_copy(update={"reason": "severe weather"})
        eligible, reason = check_eu261_eligibility(event_weather, notice_days=1)
        self.assertFalse(eligible)
        self.assertIn("extraordinary", reason)

        # US domestic flight is not EU jurisdiction
        event_us = DisruptionEvent(
            event_id="evt_us",
            type=DisruptionType.CANCELLED,
            reason="mechanical",
            flight=FlightDetails(
                carrier="AA",
                number="AA100",
                origin="JFK",
                destination="LAX",
                scheduled_departure=datetime.fromisoformat("2026-09-14T10:00:00Z"),
                scheduled_arrival=datetime.fromisoformat("2026-09-14T13:00:00Z"),
            ),
        )
        eligible, reason = check_eu261_eligibility(event_us, notice_days=1)
        self.assertFalse(eligible)

    # -------------------------------------------------------------------------
    # 6. Prompt Formatting and Parser Hallucination Protection
    # -------------------------------------------------------------------------
    def test_format_ranking_user_prompt(self):
        survivors = [self.offer_easy, self.offer_over_threshold]
        prompt = format_ranking_user_prompt(survivors, self.profile, self.deadline.isoformat(), self.original_order_total)
        self.assertIn("off_easy", prompt)
        self.assertIn("off_high", prompt)
        self.assertIn("Alex Rivera", prompt)

    def test_parse_ranking_response_sanitizes_hallucinations(self):
        survivors = [self.offer_easy, self.offer_over_threshold]

        # LLM returns a hallucinated ID "off_fake999" along with valid IDs
        raw_llm = """```json
        {
          "ranked": ["off_fake999", "off_high", "off_easy"],
          "reasons": {
            "off_high": "Best timing",
            "off_easy": "Cheapest option"
          }
        }
        ```"""
        ranked, reasons = parse_ranking_response(raw_llm, survivors, self.profile, self.original_order_total)
        ranked_ids = [o.id for o in ranked]

        self.assertNotIn("off_fake999", ranked_ids, "Hallucinated offer IDs must be discarded")
        self.assertEqual(ranked_ids, ["off_high", "off_easy"])

    def test_parse_ranking_response_fallback_on_corrupt_json(self):
        survivors = [self.offer_easy, self.offer_over_threshold]
        corrupted_response = "I think off_easy is great because it is cheap!"

        ranked, reasons = parse_ranking_response(corrupted_response, survivors, self.profile, self.original_order_total)
        self.assertEqual(len(ranked), 2)
        # Should fall back cleanly without throwing exceptions
        self.assertIn(self.offer_easy.id, [o.id for o in ranked])


if __name__ == "__main__":
    unittest.main()

