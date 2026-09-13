"""
Regression test for the stateless SMS-approval resume path.

Every other test in evals/test_scenarios.py drives the agent via
`agent.run(event, sms_reply=...)` in ONE Python call, so `ranked_offers` is
still sitting on the call stack when resume_with_sms_reply runs. That is NOT
how the real Twilio webhook works: app/main.py's POST /sms handler looks up
the pending approval by phone, builds a BRAND NEW ReboundAgent instance, and
calls `agent.resume_with_sms_reply(event_id=ev_id, reply=reply)` with no
ranked_offers/deadline/hold_order_id -- because that fresh Python object has
no memory of the original run.

Before the fix in agent/loop.py + app/db.py, this meant `ranked_offers`
stayed None, `chosen` resolved to None, and the method escalated with
"No valid offer mapped to reply." -- every real traveler SMS reply silently
failed to book. This test reproduces that exact fresh-instance / DB-reload
path and would have FAILED against the old code:
  1. ranked_offers defaults to None on the fresh agent2 instance.
  2. Nothing reloaded it from the DB (get_pending_approval_by_event_id and
     the reload branch did not exist yet).
  3. `chosen = ranked_offers[idx] if ranked_offers and ... else (... if
     ranked_offers else None)` evaluates to None.
  4. resume_with_sms_reply escalates instead of booking, and no
     confirm_booking call is ever made.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime

from agent.loop import ReboundAgent
from agent.models import ActionType, CabinClass, DisruptionEvent, FlightOffer, TravelerProfile
from app.db import Database
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio


class TestStatelessResume(unittest.TestCase):
    """Proves resume_with_sms_reply is self-sufficient on a fresh agent instance."""

    def test_fresh_instance_resume_reloads_offers_and_books(self):
        fixture_path = os.path.join(
            os.path.dirname(__file__), "fixtures", "S02_cancelled_over_threshold_yes.json"
        )
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        profile = TravelerProfile.model_validate(data["profile"])
        event = DisruptionEvent.model_validate(data["event"])

        offers = []
        for item in data["mock_duffel"]["offers"]:
            offers.append(
                FlightOffer(
                    id=item["id"],
                    carrier=item.get("carrier", "ZZ"),
                    flight_number=item.get("flight_number"),
                    departs_at=datetime.fromisoformat(item["departs_at"]),
                    arrives_at=datetime.fromisoformat(item["arrives_at"]),
                    total_amount=float(item["total_amount"]),
                    segments=int(item.get("segments", 1)),
                    cabin_class=CabinClass(item.get("cabin_class", "economy")),
                )
            )

        # These fakes are shared across both agent instances below. That
        # mirrors production, where the initial webhook run and the later
        # /sms resume both talk to the SAME real Duffel/Twilio accounts --
        # that state lives server-side, not inside the Python process. Only
        # the Database is deliberately reconstructed from scratch to prove
        # no Python-level state survives between the two calls; SQLite state
        # persists in the file exactly as it would in production.
        duffel = FakeDuffel(offers=offers)
        calendar = FakeCalendar(deadline=datetime.fromisoformat(data["calendar"]["deadline"]))
        gmail = FakeGmail()
        twilio = FakeTwilio()

        temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp_db.close()
        db_path = temp_db.name

        db1 = Database(db_path)
        agent1 = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=db1,
            profile=profile,
            original_order_total=380.0,
            run_id="run_stateless_1",
        )

        # Step 1: run WITHOUT sms_reply -- pauses in ASK state, exactly like
        # production does while it waits for the traveler's real SMS.
        paused_record = agent1.run(event)
        self.assertEqual(paused_record.action, ActionType.ASK)
        self.assertEqual(
            len([c for c in duffel.calls if c["method"] == "confirm_booking"]), 0,
            "Nothing should be booked yet -- we are still waiting on the SMS reply.",
        )

        hold_calls = [c for c in duffel.calls if c["method"] == "create_hold_order"]
        self.assertEqual(len(hold_calls), 1)
        hold_order_id = hold_calls[0]["order_id"]

        # Step 2: a BRAND NEW ReboundAgent, pointed at the SAME db file via a
        # fresh Database(...) instance -- exactly what app/main.py's /sms
        # webhook handler constructs (`agent = ReboundAgent(db=db, run_id=r_id)`)
        # -- resumes with NO ranked_offers/deadline/hold_order_id passed.
        db2 = Database(db_path)
        agent2 = ReboundAgent(
            duffel=duffel,
            calendar=calendar,
            gmail=gmail,
            twilio=twilio,
            db=db2,
            profile=profile,
            original_order_total=380.0,
            run_id="run_stateless_2",
        )

        record = agent2.resume_with_sms_reply(event.event_id, "1")

        self.assertIn(
            record.action,
            (ActionType.BOOK, ActionType.ASK_THEN_BOOK),
            f"Expected the booking to complete, got {record.action} ({record.reasoning!r})",
        )

        confirm_calls = [c for c in duffel.calls if c["method"] == "confirm_booking"]
        self.assertEqual(len(confirm_calls), 1, "confirm_booking should have been called exactly once")
        self.assertEqual(
            confirm_calls[0]["order_id"],
            hold_order_id,
            "Booking should convert the SAME hold order created in step 1, not mint a new one.",
        )


if __name__ == "__main__":
    unittest.main()
