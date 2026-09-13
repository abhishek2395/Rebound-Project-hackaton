"""
Rebound Prompts and Ranking Interface
Handles LLM prompt generation, strict JSON extraction, and survivor validation.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from agent.models import FlightOffer, TravelerProfile
from agent.policy import calculate_cost_delta, fallback_rank_offers

RANKING_SYSTEM_PROMPT = """You rank flight options for a traveler whose original flight was disrupted.
You will receive: traveler preferences, the hard deadline, and a list of
offers that ALREADY satisfy all hard constraints. Rank ALL of them.
Weigh, in order:
1. Arrival comfortably before the deadline
2. Lower cost delta
3. Preferred airlines
4. Avoiding red-eye departures (22:00–05:00 departure) if requested
5. Fewer layovers

Do not invent offers. Respond with JSON ONLY in this exact schema:
{
  "ranked": ["<offer_id_1>", "<offer_id_2>"],
  "reasons": {
    "<offer_id_1>": "<one concise sentence explaining why this is preferred>",
    "<offer_id_2>": "<one concise sentence>"
  }
}"""


def format_ranking_user_prompt(
    survivors: List[FlightOffer],
    profile: TravelerProfile,
    deadline_iso: str,
    original_order_total: float,
) -> str:
    """Formats the survivor list and preferences into the LLM user prompt."""
    offers_payload = []
    for o in survivors:
        delta = calculate_cost_delta(o, original_order_total)
        offers_payload.append({
            "offer_id": o.id,
            "carrier": o.carrier,
            "flight_number": o.flight_number or "N/A",
            "departure": o.departs_at.isoformat(),
            "arrival": o.arrives_at.isoformat(),
            "layovers": o.layovers,
            "cabin": o.cabin_class.value,
            "cost_delta_usd": delta,
            "is_redeye": o.is_redeye,
        })

    payload = {
        "traveler_name": profile.name,
        "deadline": deadline_iso,
        "preferences": {
            "preferred_airlines": profile.preferences.preferred_airlines,
            "max_layovers": profile.preferences.max_layovers,
            "avoid_redeye": profile.preferences.avoid_redeye,
            "cabin": profile.preferences.cabin.value,
        },
        "surviving_offers": offers_payload,
    }
    return json.dumps(payload, indent=2)


def parse_ranking_response(
    raw_response: str,
    survivors: List[FlightOffer],
    profile: TravelerProfile,
    original_order_total: float,
) -> Tuple[List[FlightOffer], Dict[str, str]]:
    """
    Parses LLM JSON output with guardrails:
      - Strips markdown codeblocks
      - Verifies that all ranked IDs exist in the survivors list
      - Discards any hallucinated IDs
      - Appends any missing survivors to the end
      - Falls back to deterministic ranking on parse failure
    """
    survivor_map = {o.id: o for o in survivors}
    if not survivors:
        return ([], {})

    # Extract JSON string if wrapped in ```json ... ```
    cleaned = raw_response.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)

    try:
        data = json.loads(cleaned)
        ranked_ids: List[str] = data.get("ranked", [])
        reasons: Dict[str, str] = data.get("reasons", {})

        # Filter out hallucinated IDs not present in survivors
        valid_ranked: List[FlightOffer] = []
        seen_ids = set()

        for oid in ranked_ids:
            if oid in survivor_map and oid not in seen_ids:
                valid_ranked.append(survivor_map[oid])
                seen_ids.add(oid)

        # Ensure all survivors are included (append missing ones in fallback order)
        if len(valid_ranked) < len(survivors):
            missing = [o for o in survivors if o.id not in seen_ids]
            missing_sorted = fallback_rank_offers(missing, profile, original_order_total)
            for m in missing_sorted:
                valid_ranked.append(m)
                if m.id not in reasons:
                    reasons[m.id] = "Fallback sorted option."

        if valid_ranked:
            return (valid_ranked, reasons)

    except Exception:
        pass

    # Complete fallback if JSON was invalid or unparseable
    fallback_sorted = fallback_rank_offers(survivors, profile, original_order_total)
    fallback_reasons = {
        o.id: f"Heuristic ranking: arrives {o.arrives_at.strftime('%H:%M')}, delta +${calculate_cost_delta(o, original_order_total):.2f}"
        for o in fallback_sorted
    }
    return (fallback_sorted, fallback_reasons)

