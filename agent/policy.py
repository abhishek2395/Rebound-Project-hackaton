"""
Rebound Policy & Guardrails Engine
Enforces deterministic hard constraints and mandate rules in code, not prompts.
"The LLM proposes; code disposes."
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from agent.models import (
    ActionType,
    CabinClass,
    DisruptionEvent,
    DisruptionType,
    FlightOffer,
    TravelerProfile,
)

# Standard safety buffer before hard deadline (in minutes)
DEFAULT_DEADLINE_BUFFER_MINUTES = 60

# EU/UK Airport codes for EU261 evaluation
EU_AIRPORTS = {
    "LHR", "LGW", "STN", "MAN", "EDI",  # UK (covered under UK261)
    "CDG", "ORY", "AMS", "FRA", "MUC",  # Western Europe
    "MAD", "BCN", "FCO", "MXP", "VIE",
    "ZRH", "BRU", "DUB", "CPH", "ARN",
}

EXTRAORDINARY_CIRCUMSTANCES = {
    "weather",
    "atc",
    "strike",
    "air_traffic_control",
    "security",
    "medical_emergency",
}


def compute_deadline_from_calendar(
    trip_arrival: datetime,
    destination_commitments: List[datetime],
    fallback_buffer_hours: float = 4.0,
) -> datetime:
    """
    Computes the hard arrival deadline.
    If destination meetings exist after trip arrival, the earliest meeting is the deadline.
    Otherwise, fall back to trip_arrival + fallback_buffer_hours.
    """
    future_commitments = [c for c in destination_commitments if c > trip_arrival]
    if future_commitments:
        return min(future_commitments)
    return trip_arrival + timedelta(hours=fallback_buffer_hours)


def is_delay_acceptable(
    event: DisruptionEvent,
    deadline: datetime,
    buffer_minutes: int = DEFAULT_DEADLINE_BUFFER_MINUTES,
) -> bool:
    """
    Evaluates whether a flight delay still arrives before deadline - buffer.
    If so, no rebooking is necessary (action: notify_only).
    """
    if event.type != DisruptionType.DELAYED:
        return False

    delay = event.delay_minutes or 0
    delayed_arrival = event.flight.scheduled_arrival + timedelta(minutes=delay)
    latest_allowed_arrival = deadline - timedelta(minutes=buffer_minutes)

    return delayed_arrival <= latest_allowed_arrival


def filter_survivors(
    offers: List[FlightOffer],
    profile: TravelerProfile,
    deadline: datetime,
    buffer_minutes: int = DEFAULT_DEADLINE_BUFFER_MINUTES,
) -> List[FlightOffer]:
    """
    Hard constraint filter executed entirely in deterministic Python.
    Enforces:
      1. Arrival <= deadline - buffer
      2. Layovers <= max_layovers
      3. Cabin class is equal to or an upgrade from profile preference
    """
    latest_allowed_arrival = deadline - timedelta(minutes=buffer_minutes)
    survivors: List[FlightOffer] = []

    for offer in offers:
        # Constraint 1: Must arrive before the deadline buffer
        if offer.arrives_at > latest_allowed_arrival:
            continue

        # Constraint 2: Layover limit
        if offer.layovers > profile.preferences.max_layovers:
            continue

        # Constraint 3: Cabin constraint
        if profile.preferences.cabin == CabinClass.ECONOMY:
            # Any cabin is acceptable
            pass
        elif profile.preferences.cabin == CabinClass.PREMIUM_ECONOMY:
            if offer.cabin_class == CabinClass.ECONOMY:
                continue
        elif profile.preferences.cabin == CabinClass.BUSINESS:
            if offer.cabin_class in (CabinClass.ECONOMY, CabinClass.PREMIUM_ECONOMY):
                continue

        survivors.append(offer)

    return survivors


def calculate_cost_delta(offer: FlightOffer, original_order_total: float) -> float:
    """Calculates the net cost difference between the new offer and original ticket."""
    return round(offer.total_amount - original_order_total, 2)


def evaluate_mandate(
    chosen_offer: Optional[FlightOffer],
    profile: TravelerProfile,
    original_order_total: float,
) -> Tuple[ActionType, float, str]:
    """
    Evaluates mandate rules:
      - No options -> ESCALATE
      - Cost delta > spend_ceiling -> ESCALATE
      - Cost delta <= approval_threshold and auto_book=True -> BOOK
      - Otherwise -> ASK
    """
    if not chosen_offer:
        return (ActionType.ESCALATE, 0.0, "No viable flight options met hard constraints.")

    delta = calculate_cost_delta(chosen_offer, original_order_total)

    # Hard ceiling check
    if delta > profile.mandate.spend_ceiling_usd:
        return (
            ActionType.ESCALATE,
            delta,
            f"Cost delta +${delta:.2f} exceeds spend ceiling of ${profile.mandate.spend_ceiling_usd:.2f}.",
        )

    # Auto-book check
    if profile.mandate.auto_book and delta <= profile.mandate.approval_threshold_usd:
        return (
            ActionType.BOOK,
            delta,
            f"Cost delta +${delta:.2f} is within approval threshold of ${profile.mandate.approval_threshold_usd:.2f}.",
        )

    # Requires traveler approval
    reason = (
        f"Cost delta +${delta:.2f} exceeds approval threshold (${profile.mandate.approval_threshold_usd:.2f})"
        if delta > profile.mandate.approval_threshold_usd
        else "Traveler profile mandate requires confirmation before booking (auto_book=False)."
    )
    return (ActionType.ASK, delta, reason)


def fallback_rank_offers(
    survivors: List[FlightOffer],
    profile: TravelerProfile,
    original_order_total: float,
) -> List[FlightOffer]:
    """
    Deterministic fallback heuristic if LLM ranking is unavailable or hallucinates.
    Sort priority:
      1. Preferred airline (+1000 score bonus)
      2. Non-redeye (+500 score bonus if avoid_redeye=True)
      3. Fewer layovers
      4. Earliest arrival
      5. Lowest cost delta
    """
    def score_offer(offer: FlightOffer) -> Tuple[int, int, int, datetime, float]:
        airline_score = 0 if offer.carrier in profile.preferences.preferred_airlines else 1
        redeye_score = 1 if (profile.preferences.avoid_redeye and offer.is_redeye) else 0
        layover_score = offer.layovers
        arrival = offer.arrives_at
        cost_delta = calculate_cost_delta(offer, original_order_total)
        return (airline_score, redeye_score, layover_score, arrival, cost_delta)

    return sorted(survivors, key=score_offer)


def check_eu261_eligibility(
    event: DisruptionEvent,
    notice_days: int = 0,
) -> Tuple[bool, str]:
    """
    Determines if a cancellation or delay qualifies for compensation under EU261 / UK261.
    Rules:
      - Departure from EU/UK airport OR EU carrier.
      - Cancellation notice given < 14 days before flight.
      - Cause was NOT extraordinary (weather, ATC, strikes are exempt).
    """
    if event.type not in (DisruptionType.CANCELLED, DisruptionType.DELAYED):
        return (False, "Event type is not a cancellation or long delay.")

    if event.type == DisruptionType.DELAYED and (event.delay_minutes or 0) < 180:
        return (False, "Delay is under 3 hours; not eligible for compensation.")

    # Check airport / carrier jurisdiction
    is_eu_departure = event.flight.origin.upper() in EU_AIRPORTS
    is_eu_carrier = event.flight.carrier.upper() in {"ZZ", "BA", "AF", "LH", "KL", "IB"}

    if not (is_eu_departure or is_eu_carrier):
        return (False, "Flight is not within EU/UK jurisdiction (non-EU origin and non-EU carrier).")

    # Check extraordinary cause
    reason = (event.reason or "").lower()
    for extra in EXTRAORDINARY_CIRCUMSTANCES:
        if extra in reason:
            return (False, f"Cause '{event.reason}' is classified as extraordinary circumstances.")

    # Check notice period
    if notice_days >= 14:
        return (False, f"Notice of {notice_days} days exceeds the 14-day statutory limit.")

    return (True, "Eligible under Regulation (EC) No 261/2004 or UK equivalent.")

