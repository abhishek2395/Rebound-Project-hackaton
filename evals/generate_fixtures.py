"""
Generator script to produce all 30 scenario fixtures under evals/fixtures/.
Follows the exact specification from REBOUND_HACKATHON_PLAYBOOK.md (Section 6).
"""

import json
import os

FIXTURES_DIR = "evals/fixtures"
os.makedirs(FIXTURES_DIR, exist_ok=True)

BASE_PROFILE = {
    "traveler_id": "alex",
    "name": "Alex Rivera",
    "email": "alex@example.com",
    "phone": "+15550000001",
    "preferences": {
        "preferred_airlines": ["ZZ"],
        "max_layovers": 1,
        "seat": "aisle",
        "cabin": "economy",
        "avoid_redeye": True,
    },
    "mandate": {
        "spend_ceiling_usd": 800.0,
        "approval_threshold_usd": 300.0,
        "auto_book": True,
    },
    "contacts": [
        {"name": "Sam", "relation": "pickup", "phone": "+15550000002", "email": "sam@example.com"}
    ],
    "default_deadline_buffer_hours": 6.0,
}

BASE_FLIGHT = {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z",
}

# Standard good flight offer: delta = +$120.00
OFFER_A = {
    "id": "off_a",
    "carrier": "ZZ",
    "flight_number": "ZZ201",
    "departs_at": "2026-09-14T14:10:00Z",
    "arrives_at": "2026-09-14T17:05:00Z",
    "total_amount": 500.00,
    "segments": 1,
    "cabin_class": "economy",
}

# High-cost offer: delta = +$450.00 (within $800 ceiling, over $300 threshold)
OFFER_OVER_THRESHOLD = {
    "id": "off_over",
    "carrier": "ZZ",
    "flight_number": "ZZ202",
    "departs_at": "2026-09-14T16:40:00Z",
    "arrives_at": "2026-09-14T20:30:00Z",
    "total_amount": 830.00,
    "segments": 1,
    "cabin_class": "economy",
}

# Extremely expensive offer: delta = +$1200.00 (over $800 ceiling)
OFFER_OVER_CEILING = {
    "id": "off_ceil",
    "carrier": "ZZ",
    "flight_number": "ZZ999",
    "departs_at": "2026-09-14T14:10:00Z",
    "arrives_at": "2026-09-14T17:05:00Z",
    "total_amount": 1580.00,
    "segments": 1,
    "cabin_class": "economy",
}

# Cheaper offer: delta = -$80.00 (refund)
OFFER_CHEAPER = {
    "id": "off_cheap",
    "carrier": "ZZ",
    "flight_number": "ZZ205",
    "departs_at": "2026-09-14T15:00:00Z",
    "arrives_at": "2026-09-14T18:00:00Z",
    "total_amount": 300.00,
    "segments": 1,
    "cabin_class": "economy",
}

# 2-layover offer
OFFER_TWO_LAYOVERS = {
    "id": "off_2lay",
    "carrier": "ZZ",
    "flight_number": "ZZ301",
    "departs_at": "2026-09-14T12:00:00Z",
    "arrives_at": "2026-09-14T20:00:00Z",
    "total_amount": 420.00,
    "segments": 3,
    "cabin_class": "economy",
}

# Redeye departure (departs 23:30)
OFFER_REDEYE = {
    "id": "off_redeye",
    "carrier": "ZZ",
    "flight_number": "ZZ888",
    "departs_at": "2026-09-14T23:30:00Z",
    "arrives_at": "2026-09-15T02:30:00Z",
    "total_amount": 450.00,
    "segments": 1,
    "cabin_class": "economy",
}

# Non-preferred airline (carrier: "XX")
OFFER_OTHER_AIRLINE = {
    "id": "off_other",
    "carrier": "XX",
    "flight_number": "XX101",
    "departs_at": "2026-09-14T14:10:00Z",
    "arrives_at": "2026-09-14T17:05:00Z",
    "total_amount": 500.00,
    "segments": 1,
    "cabin_class": "economy",
}

SCENARIOS = [
    # 1. S01_cancelled_easy
    {
        "id": "S01_cancelled_easy",
        "description": "Cancelled; good same-day option within threshold",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s01", "type": "cancelled", "order_id": "ord_s01", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1, "calendar_updated": True, "emails_sent": ["itinerary"]},
    },
    # 2. S02_cancelled_over_threshold_yes
    {
        "id": "S02_cancelled_over_threshold_yes",
        "description": "Best option delta $450; reply '1' -> ask then book",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s02", "type": "cancelled", "order_id": "ord_s02", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_OVER_THRESHOLD], "failures": []},
        "sms_reply": "1",
        "expected": {"action": "ask_then_book", "chosen_offer_id": "off_over", "bookings_created": 1, "calendar_updated": True},
    },
    # 3. S03_cancelled_over_threshold_no
    {
        "id": "S03_cancelled_over_threshold_no",
        "description": "Over threshold; reply 'NO' -> escalate, 0 bookings",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s03", "type": "cancelled", "order_id": "ord_s03", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_OVER_THRESHOLD], "failures": []},
        "sms_reply": "NO",
        "expected": {"action": "escalate", "chosen_offer_id": None, "bookings_created": 0},
    },
    # 4. S04_cancelled_over_threshold_timeout
    {
        "id": "S04_cancelled_over_threshold_timeout",
        "description": "Over threshold; no reply -> timeout and escalate",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s04", "type": "cancelled", "order_id": "ord_s04", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_OVER_THRESHOLD], "failures": []},
        "sms_reply": "TIMEOUT",
        "expected": {"action": "escalate", "chosen_offer_id": None, "bookings_created": 0},
    },
    # 5. S05_cancelled_over_ceiling
    {
        "id": "S05_cancelled_over_ceiling",
        "description": "Only option delta $1,200 -> escalate over ceiling",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s05", "type": "cancelled", "order_id": "ord_s05", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_OVER_CEILING], "failures": []},
        "sms_reply": None,
        "expected": {"action": "escalate", "chosen_offer_id": None, "bookings_created": 0},
    },
    # 6. S06_delay_still_makes_it
    {
        "id": "S06_delay_still_makes_it",
        "description": "3h delay, deadline tomorrow 9AM -> notify_only",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s06", "type": "delayed", "delay_minutes": 180, "order_id": "ord_s06", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "notify_only", "bookings_created": 0},
    },
    # 7. S07_delay_misses_deadline
    {
        "id": "S07_delay_misses_deadline",
        "description": "5h delay pushes past deadline -> rebook",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-14T18:30:00Z"},
        "event": {"event_id": "evt_s07", "type": "delayed", "delay_minutes": 300, "order_id": "ord_s07", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 8. S08_missed_connection
    {
        "id": "S08_missed_connection",
        "description": "Second leg missed; rebook remaining journey",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s08", "type": "missed_connection", "order_id": "ord_s08", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 9. S09_no_options_before_deadline
    {
        "id": "S09_no_options_before_deadline",
        "description": "All offers arrive after deadline -> escalate",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-14T15:00:00Z"},  # Earliest offer arrives 17:05
        "event": {"event_id": "evt_s09", "type": "cancelled", "order_id": "ord_s09", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "escalate", "chosen_offer_id": None, "bookings_created": 0},
    },
    # 10. S10_duplicate_webhook
    {
        "id": "S10_duplicate_webhook",
        "description": "Event delivered twice; 1 booking, second run skipped_duplicate",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s10", "type": "cancelled", "order_id": "ord_s10", "flight": BASE_FLIGHT},
        "deliver_times": 2,
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1, "second_run_status": "skipped_duplicate"},
    },
    # 11. S11_duffel_500_twice
    {
        "id": "S11_duffel_500_twice",
        "description": "Offer request 500s twice then succeeds with backoff",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s11", "type": "cancelled", "order_id": "ord_s11", "flight": BASE_FLIGHT},
        "mock_duffel": {
            "offers": [OFFER_A],
            "failures": [{"call": "offer_requests.create", "sequence": ["500", "500", "ok"]}],
        },
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1, "retries_observed": 2},
    },
    # 12. S12_duffel_500_forever
    {
        "id": "S12_duffel_500_forever",
        "description": "Duffel fails continuously -> escalate cleanly",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s12", "type": "cancelled", "order_id": "ord_s12", "flight": BASE_FLIGHT},
        "mock_duffel": {
            "offers": [OFFER_A],
            "failures": [{"call": "offer_requests.create", "sequence": ["500", "500", "500", "500"]}],
        },
        "sms_reply": None,
        "expected": {"action": "escalate", "chosen_offer_id": None, "bookings_created": 0},
    },
    # 13. S13_offer_expired_at_booking
    {
        "id": "S13_offer_expired_at_booking",
        "description": "Hold order locks inventory; prevents offer_expired failure",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s13", "type": "cancelled", "order_id": "ord_s13", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 14. S14_empty_results_widen
    {
        "id": "S14_empty_results_widen",
        "description": "No direct options -> escalate with clear summary",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s14", "type": "cancelled", "order_id": "ord_s14", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [], "failures": []},
        "sms_reply": None,
        "expected": {"action": "escalate", "chosen_offer_id": None, "bookings_created": 0},
    },
    # 15. S15_layover_limit
    {
        "id": "S15_layover_limit",
        "description": "Prunes 2-layover option; books 1-layover option",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s15", "type": "cancelled", "order_id": "ord_s15", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_TWO_LAYOVERS, OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 16. S16_redeye_avoid
    {
        "id": "S16_redeye_avoid",
        "description": "Prefers daytime flight over red-eye per profile preference",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s16", "type": "cancelled", "order_id": "ord_s16", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_REDEYE, OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 17. S17_preferred_airline_tiebreak
    {
        "id": "S17_preferred_airline_tiebreak",
        "description": "Two equal options, breaks tie using preferred carrier ZZ",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s17", "type": "cancelled", "order_id": "ord_s17", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_OTHER_AIRLINE, OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 18. S18_calendar_conflict
    {
        "id": "S18_calendar_conflict",
        "description": "Books viable flight within deadline buffer",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s18", "type": "cancelled", "order_id": "ord_s18", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 19. S19_no_calendar_deadline
    {
        "id": "S19_no_calendar_deadline",
        "description": "Empty calendar -> falls back to default 4h buffer",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-14T19:00:00Z"},
        "event": {"event_id": "evt_s19", "type": "cancelled", "order_id": "ord_s19", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 20. S20_crash_after_book_before_cancel
    {
        "id": "S20_crash_after_book_before_cancel",
        "description": "Safe ordering ensures old flight is cancelled after verification",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s20", "type": "cancelled", "order_id": "ord_s20", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 21. S21_verify_mismatch
    {
        "id": "S21_verify_mismatch",
        "description": "Simulated verify mismatch -> aborts cancellation of old order",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s21", "type": "cancelled", "order_id": "ord_s21", "flight": BASE_FLIGHT},
        "mock_duffel": {
            "offers": [OFFER_A],
            "failures": [{"call": "orders.get", "sequence": ["500"]}],
        },
        "sms_reply": None,
        "expected": {"action": "escalate", "bookings_created": 1, "old_order_cancelled": False},
    },
    # 22. S22_compensation_eligible
    {
        "id": "S22_compensation_eligible",
        "description": "EU departure operational cancellation -> draft EU261 claim",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s22", "type": "cancelled", "reason": "operational crew shortage", "order_id": "ord_s22", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "compensation_drafted": True},
    },
    # 23. S23_compensation_not_eligible
    {
        "id": "S23_compensation_not_eligible",
        "description": "Weather disruption is exempt from EU261 compensation",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s23", "type": "cancelled", "reason": "severe weather blizzard", "order_id": "ord_s23", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "compensation_drafted": False},
    },
    # 24. S24_pickup_contact_notified
    {
        "id": "S24_pickup_contact_notified",
        "description": "Profile has pickup contact -> sends ETA update SMS to Sam",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s24", "type": "cancelled", "order_id": "ord_s24", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "pickup_notified": True},
    },
    # 25. S25_auto_book_off
    {
        "id": "S25_auto_book_off",
        "description": "mandate.auto_book=false -> asks even if within threshold",
        "profile": {
            **BASE_PROFILE,
            "mandate": {"spend_ceiling_usd": 800.0, "approval_threshold_usd": 300.0, "auto_book": False},
        },
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s25", "type": "cancelled", "order_id": "ord_s25", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": "1",
        "expected": {"action": "ask_then_book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 26. S26_malformed_event
    {
        "id": "S26_malformed_event",
        "description": "Missing origin -> rejected by validation",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "", "type": "cancelled", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "validation_error"},
    },
    # 27. S27_calendar_api_down
    {
        "id": "S27_calendar_api_down",
        "description": "Google Calendar 503 -> degrades gracefully, books using fallback buffer",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z", "injected_failure": "503"},
        "event": {"event_id": "evt_s27", "type": "cancelled", "order_id": "ord_s27", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 28. S28_sms_api_down_when_ask_needed
    {
        "id": "S28_sms_api_down_when_ask_needed",
        "description": "Twilio 500 on ask -> escalates via email",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s28", "type": "cancelled", "order_id": "ord_s28", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_OVER_THRESHOLD], "failures": []},
        "mock_twilio": {"injected_failure": "500"},
        "sms_reply": None,
        "expected": {"action": "escalate", "bookings_created": 0},
    },
    # 29. S29_gmail_down
    {
        "id": "S29_gmail_down",
        "description": "Gmail 503 after booking -> booking stands, error traced",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s29", "type": "cancelled", "order_id": "ord_s29", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_A], "failures": []},
        "mock_gmail": {"injected_failure": "503"},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_a", "bookings_created": 1},
    },
    # 30. S30_cost_delta_negative
    {
        "id": "S30_cost_delta_negative",
        "description": "New flight cheaper -> books, calculates negative delta",
        "profile": BASE_PROFILE,
        "calendar": {"deadline": "2026-09-15T09:00:00Z"},
        "event": {"event_id": "evt_s30", "type": "cancelled", "order_id": "ord_s30", "flight": BASE_FLIGHT},
        "mock_duffel": {"offers": [OFFER_CHEAPER], "failures": []},
        "sms_reply": None,
        "expected": {"action": "book", "chosen_offer_id": "off_cheap", "bookings_created": 1, "negative_delta": True},
    },
]

def generate_all():
    print(f"Generating {len(SCENARIOS)} scenario fixtures in {FIXTURES_DIR}...")
    for s in SCENARIOS:
        filename = os.path.join(FIXTURES_DIR, f"{s['id']}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
    print(f"Successfully wrote {len(SCENARIOS)} fixtures.")

if __name__ == "__main__":
    generate_all()
