"""
Rebound Agent State Machine
Coordinates the 9-step disruption recovery graph with Human-in-the-Loop (HITL)
approval pausing, hold order price locking, and ordered irreversible actions.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from agent.models import (
    ActionType,
    ConfirmedBooking,
    DecisionRecord,
    DisruptionEvent,
    DisruptionType,
    FlightOffer,
    HoldOrder,
    TraceStatus,
    TravelerProfile,
)
from agent.policy import (
    check_eu261_eligibility,
    compute_deadline_from_calendar,
    evaluate_mandate,
    fallback_rank_offers,
    filter_survivors,
    is_delay_acceptable,
)
from agent.prompts import (
    format_ranking_user_prompt,
    parse_ranking_response,
)
from app.db import Database
from clients.base import BaseClient, NonRetryableAPIError, RetryableAPIError
from clients.fakes import FakeCalendar, FakeDuffel, FakeGmail, FakeTwilio
from tracing.tracer import Tracer

logger = logging.getLogger("rebound.agent")


def flight_code(carrier: str, flight_number: Optional[str]) -> str:
    """ZZ + ZZ202 is ZZ202, not ZZZZ202 — feeds vary on whether the number carries its carrier."""
    number = (flight_number or "").strip()
    carrier = (carrier or "").strip()
    if not number:
        return carrier
    return number if number.upper().startswith(carrier.upper()) else f"{carrier}{number}"


class ReboundAgent:
    """The 9-step Rebound Disruption Recovery Orchestrator."""
    def __init__(
        self,
        duffel: Optional[BaseClient] = None,
        calendar: Optional[BaseClient] = None,
        gmail: Optional[BaseClient] = None,
        twilio: Optional[BaseClient] = None,
        db: Optional[Database] = None,
        profile: Optional[TravelerProfile] = None,
        original_order_total: float = 380.0,
        run_id: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.duffel = duffel or FakeDuffel()
        self.calendar = calendar or FakeCalendar()
        self.gmail = gmail or FakeGmail()
        self.twilio = twilio or FakeTwilio()
        self.db = db or Database("rebound.db")
        self.profile = profile or self._load_default_profile()
        self.original_order_total = original_order_total
        self.dry_run = dry_run
        
        self.run_id = run_id or f"run_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
        self.tracer = Tracer(self.run_id)

    def _load_default_profile(self) -> TravelerProfile:
        profile_path = "profiles/alex.json"
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                return TravelerProfile.model_validate_json(f.read())
        return TravelerProfile(
            traveler_id="alex",
            name="Alex Rivera",
            email="alex@example.com",
            phone="+15550000001",
        )

    # -------------------------------------------------------------------------
    # The 9 Graph Nodes
    # -------------------------------------------------------------------------
    def run(
        self,
        event: DisruptionEvent,
        sms_reply: Optional[str] = None,
    ) -> DecisionRecord:
        """
        Executes the Rebound agent loop:
          1. ingest -> 2. context -> 3. assess -> 4. search -> 5. rank ->
          6. decide -> [HITL interrupt/hold] -> 7. act -> 8. verify -> 9. report
        """
        # ---------------------------------------------------------------------
        # Step 1: Ingest & Deduplication
        # ---------------------------------------------------------------------
        with self.tracer.span("ingest", "db.record_event", {"event_id": event.event_id}) as s:
            inserted = self.db.record_event(
                event_id=event.event_id,
                event_type=event.type.value,
                order_id=event.order_id,
            )
            if not inserted:
                s["summary"] = f"Duplicate event {event.event_id} detected; skipping execution."
                s["status"] = TraceStatus.SKIPPED
                self.tracer.log_entry(
                    step="ingest:skipped_duplicate",
                    tool="db.record_event",
                    output_summary="Duplicate event ignored for idempotency.",
                )
                return DecisionRecord(
                    event_id=event.event_id,
                    action=ActionType.SKIPPED_DUPLICATE,
                    reasoning="Duplicate event delivered. Ignored to prevent duplicate bookings.",
                )
            s["summary"] = f"Event {event.event_id} ({event.type.value}) ingested successfully."

        # ---------------------------------------------------------------------
        # Step 2: Context & Dynamic Deadline Resolution
        # ---------------------------------------------------------------------
        with self.tracer.span("context", "calendar.get_destination_deadline", {"event_id": event.event_id}) as s:
            trip_arrival = event.flight.scheduled_arrival
            try:
                deadline = self.calendar.get_destination_deadline(trip_arrival, default_buffer_hours=self.profile.default_deadline_buffer_hours)
            except Exception as e:
                logger.warning("Calendar API unavailable (%s); using fallback deadline.", e)
                deadline = trip_arrival + timedelta(hours=self.profile.default_deadline_buffer_hours)
                self.tracer.log_entry(step="context:calendar_degraded", tool="calendar.get", status=TraceStatus.RETRY, output_summary=str(e))

            s["summary"] = f"Hard arrival deadline resolved to {deadline.isoformat()}"

        # ---------------------------------------------------------------------
        # Step 3: Assess Impact
        # ---------------------------------------------------------------------
        with self.tracer.span("assess", "policy.is_delay_acceptable", {"type": event.type.value}) as s:
            if event.type == DisruptionType.DELAYED and is_delay_acceptable(event, deadline):
                s["summary"] = "Flight delay still meets arrival deadline buffer. No rebooking required."
                self.twilio.send_sms(
                    to_phone=self.profile.phone,
                    body=f"[Rebound] Flight {event.flight.number} is delayed by {event.delay_minutes}m, but you will still arrive before your deadline. No rebooking needed.",
                    sms_type="delay_notice",
                )
                record = DecisionRecord(
                    event_id=event.event_id,
                    action=ActionType.NOTIFY_ONLY,
                    deadline=deadline,
                    reasoning=f"Delay of {event.delay_minutes}m does not violate the commitment deadline.",
                )
                self.db.record_run(self.run_id, event.event_id, record.action.value, trace_path=self.tracer.file_path)
                return record
            s["summary"] = "Disruption impacts trip. Proceeding to search alternative flights."

        # ---------------------------------------------------------------------
        # Step 4: Search Flights (with Exponential Backoff Retries)
        # ---------------------------------------------------------------------
        raw_offers: List[FlightOffer] = []
        dep_date = event.flight.scheduled_departure.strftime("%Y-%m-%d")
        search_max_attempts = 3

        with self.tracer.span("search", "duffel.offer_requests.create", {"origin": event.flight.origin, "destination": event.flight.destination}) as s:
            for attempt in range(1, search_max_attempts + 1):
                try:
                    raw_offers = self.duffel.search_offers(
                        origin=event.flight.origin,
                        destination=event.flight.destination,
                        departure_date=dep_date,
                        cabin_class=self.profile.preferences.cabin.value,
                    )
                    s["summary"] = f"Retrieved {len(raw_offers)} candidate offers from Duffel."
                    if attempt > 1:
                        self.tracer.log_entry(step="search:ok", tool="duffel.offer_requests.create", output_summary="Succeeded after retry.")
                    break
                except RetryableAPIError as e:
                    self.tracer.log_entry(
                        step="search:retry",
                        tool="duffel.offer_requests.create",
                        status=TraceStatus.RETRY,
                        output_summary=f"Duffel 5xx/429 backoff retry {attempt}/{search_max_attempts}: {e}",
                    )
                    if attempt == search_max_attempts:
                        logger.error("Duffel search exhausted retries: %s", e)
                        s["summary"] = f"Duffel search error: {str(e)}"
                        s["status"] = TraceStatus.ERROR
                        self.twilio.send_sms(
                            to_phone=self.profile.phone,
                            body=f"[Rebound] {event.flight.number} disrupted, but flight search failed ({str(e)}). Please call the airline.",
                            sms_type="escalation",
                        )
                        return DecisionRecord(
                            event_id=event.event_id,
                            action=ActionType.ESCALATE,
                            reasoning=f"Flight search unavailable after retries: {str(e)}",
                        )
                except Exception as e:
                    logger.error("Duffel search permanent failure: %s", e)
                    s["summary"] = f"Duffel error: {str(e)}"
                    s["status"] = TraceStatus.ERROR
                    return DecisionRecord(
                        event_id=event.event_id,
                        action=ActionType.ESCALATE,
                        reasoning=f"Flight search failed: {str(e)}",
                    )

        # ---------------------------------------------------------------------
        # Step 5: Rank Offers (Deterministic Code Filter + LLM / Heuristic Rank)
        # ---------------------------------------------------------------------
        survivors = filter_survivors(raw_offers, self.profile, deadline)
        if not survivors:
            self.tracer.log_entry(
                step="rank:0_survivors",
                tool="policy.filter_survivors",
                output_summary="0 offers survived hard constraints (deadline buffer or layovers).",
            )
            return self._handle_escalation(event, deadline, "No flights found arriving before deadline within constraints.")

        # Rank survivors using heuristic sorter or LLM
        ranked_offers = fallback_rank_offers(survivors, self.profile, self.original_order_total)
        chosen_offer = ranked_offers[0]
        alternatives = [o.id for o in ranked_offers[1:3]]

        with self.tracer.span("rank", "policy.rank_survivors", {"survivors_count": len(survivors)}) as s:
            s["summary"] = f"Top choice {chosen_offer.id} (+${chosen_offer.total_amount - self.original_order_total:.2f}) chosen among {len(survivors)} survivors."

        # ---------------------------------------------------------------------
        # Step 6: Decide (Mandate Policy Check & HITL Approval)
        # ---------------------------------------------------------------------
        action, cost_delta, mandate_reason = evaluate_mandate(chosen_offer, self.profile, self.original_order_total)

        if action == ActionType.ESCALATE:
            self.tracer.log_entry(step="decide:escalate", tool="policy.evaluate_mandate", output_summary=mandate_reason)
            return self._handle_escalation(event, deadline, mandate_reason, chosen_offer)

        if action == ActionType.ASK:
            self.tracer.log_entry(step="decide:ask", tool="policy.evaluate_mandate", output_summary=mandate_reason)
            return self._handle_approval_flow(
                event=event,
                deadline=deadline,
                chosen_offer=chosen_offer,
                ranked_offers=ranked_offers,
                cost_delta=cost_delta,
                reasoning=mandate_reason,
                sms_reply=sms_reply,
            )

        # ---------------------------------------------------------------------
        # Steps 7-9: Act, Verify, Report (Auto-Book Path)
        # ---------------------------------------------------------------------
        return self._execute_booking_and_side_effects(
            event=event,
            deadline=deadline,
            chosen_offer=chosen_offer,
            cost_delta=cost_delta,
            reasoning=mandate_reason,
            alternatives=alternatives,
            hold_order_id=None,  # Auto-book path never creates a hold order.
        )

    # -------------------------------------------------------------------------
    # Approval Flow & Hold-Order Pattern
    # -------------------------------------------------------------------------
    def _handle_approval_flow(
        self,
        event: DisruptionEvent,
        deadline: datetime,
        chosen_offer: FlightOffer,
        ranked_offers: List[FlightOffer],
        cost_delta: float,
        reasoning: str,
        sms_reply: Optional[str],
    ) -> DecisionRecord:
        """Handles creating a hold order, sending SMS, and processing replies."""
        # Hold Order pattern to prevent 15-minute offer expiration
        hold_order = self.duffel.create_hold_order(chosen_offer.id, self.profile)
        self.tracer.log_entry(
            step="hold_order:created",
            tool="duffel.orders.create_hold",
            output_summary=f"Held inventory for offer {chosen_offer.id} (Order {hold_order.order_id})",
        )

        top_2 = ranked_offers[:2]
        options_text = []
        for idx, opt in enumerate(top_2, start=1):
            opt_delta = opt.total_amount - self.original_order_total
            options_text.append(f"{idx}) {flight_code(opt.carrier, opt.flight_number)} arr {opt.arrives_at.strftime('%H:%M')}, +${opt_delta:.2f}")

        # Dynamic reply-instructions: the SMS must not offer "Reply 2" when
        # only one survivor made it past the hard filters. Bug caught during
        # the live WhatsApp round-trip on 2026-09-13.
        if len(top_2) == 1:
            reply_line = "Reply 1 to book, or NO."
        else:
            reply_line = "Reply 1 or 2 to book, or NO."

        sms_body = (
            f"[Rebound] Your {event.flight.carrier}{event.flight.number} was cancelled. Options:\n"
            + "\n".join(options_text)
            + f"\n{reply_line} Expires in 10 min."
        )

        # Put the options the traveler was actually shown into the trace, so any
        # reader of the run — the viewer included — can state them from evidence
        # rather than reconstructing plausible-looking flights.
        self.tracer.log_entry(
            step="options:presented",
            tool="policy.rank_survivors",
            output_summary=" | ".join(
                f"{idx}) {flight_code(o.carrier, o.flight_number)} "
                f"{o.origin or event.flight.origin}-{o.destination or event.flight.destination} "
                f"dep {o.departs_at.strftime('%H:%M')} arr {o.arrives_at.strftime('%H:%M')} "
                f"{o.total_amount - self.original_order_total:+.2f}"
                for idx, o in enumerate(top_2, start=1)
            ),
        )

        try:
            self.twilio.send_sms(to_phone=self.profile.phone, body=sms_body, sms_type="approval_request")
        except Exception as e:
            logger.error("Twilio SMS failed during approval request: %s", e)
            self.tracer.log_entry(step="sms:error_fallback_email", tool="twilio.send", status=TraceStatus.RETRY, output_summary=str(e))
            # Fallback to email if SMS gateway fails (Scenario S28)
            self.gmail.send_itinerary_email(
                to_email=self.profile.email,
                subject=f"[Action Required] Rebooking Approval: {event.flight.carrier}{event.flight.number}",
                body=sms_body,
            )
            return DecisionRecord(
                event_id=event.event_id,
                action=ActionType.ESCALATE,
                reasoning="SMS gateway unavailable; escalated approval via email.",
            )

        # Record pending state in SQLite. Store enough of each offer (not just the
        # thin id/carrier/total_amount used for the SMS body) that a brand-new
        # ReboundAgent instance -- with no in-memory ranked_offers -- can fully
        # reconstruct FlightOffer objects later when the real Twilio webhook
        # reply arrives (see resume_with_sms_reply).
        rich_fields = {
            "id", "carrier", "flight_number", "origin", "destination",
            "departs_at", "arrives_at", "total_amount", "currency",
            "segments", "cabin_class",
        }
        options_dicts = [o.model_dump(mode="json", include=rich_fields) for o in top_2]
        self.db.save_pending_approval(
            event_id=event.event_id,
            run_id=self.run_id,
            traveler_phone=self.profile.phone,
            hold_order_id=hold_order.order_id,
            options=options_dicts,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            # The resumed run rebuilds the original event from this, so the booking
            # it produces stays attributable to the disruption that caused it.
            event_json=event.model_dump_json(),
        )

        # If an SMS reply is already provided (e.g. In eval harness or fast reply)
        if sms_reply:
            return self.resume_with_sms_reply(
                event.event_id, sms_reply, deadline, ranked_offers,
                hold_order_id=hold_order.order_id,
                event=event,
            )

        # In live production, agent pauses here until Twilio webhook hits /sms
        self.tracer.log_entry(
            step="interrupt:paused_for_approval",
            tool="langgraph.interrupt",
            output_summary="Graph execution yielded to human SMS authorization.",
        )
        record = DecisionRecord(
            event_id=event.event_id,
            action=ActionType.ASK,
            chosen_offer_id=chosen_offer.id,
            cost_delta_usd=cost_delta,
            arrives_at=chosen_offer.arrives_at,
            deadline=deadline,
            reasoning=reasoning,
            alternatives=[o.id for o in ranked_offers[1:3]],
        )
        # A run parked awaiting a human is still a run: record it so it is
        # visible to /api/runs and the trace viewer while it waits, not only
        # once someone replies.
        self.db.record_run(
            run_id=self.run_id,
            event_id=event.event_id,
            action=record.action.value,
            chosen_offer_id=chosen_offer.id,
            cost_delta_usd=cost_delta,
            trace_path=self.tracer.file_path,
        )
        return record

    def resume_with_sms_reply(
        self,
        event_id: str,
        reply: str,
        deadline: Optional[datetime] = None,
        ranked_offers: Optional[List[FlightOffer]] = None,
        hold_order_id: Optional[str] = None,
        event: Optional[DisruptionEvent] = None,
    ) -> DecisionRecord:
        """
        Resumes execution when a traveler sends an SMS reply.

        This must be self-sufficient: in production this is called on a BRAND
        NEW ReboundAgent instance (created fresh by the /sms webhook handler),
        so it cannot rely on ranked_offers/deadline/hold_order_id still being
        present on the call stack -- they arrive as None. When that happens we
        reload everything we need from the pending_approvals row that
        _handle_approval_flow persisted to SQLite.
        """
        clean_reply = reply.strip().upper()

        original_event: Optional[DisruptionEvent] = event
        if ranked_offers is None:
            pending = self.db.get_pending_approval_by_event_id(event_id)
            if pending:
                ranked_offers = [FlightOffer(**opt) for opt in pending["options"]]
                if hold_order_id is None:
                    hold_order_id = pending.get("hold_order_id")
                if pending.get("event_json"):
                    original_event = DisruptionEvent.model_validate_json(pending["event_json"])

        self.tracer.log_entry(
            step=f"approval:received:{clean_reply}",
            tool="twilio.inbound_webhook",
            output_summary=f"Received traveler SMS response: '{clean_reply}'",
        )

        if clean_reply in ("NO", "N"):
            self.db.resolve_pending_approval(event_id, status="rejected")
            self.twilio.send_sms(
                to_phone=self.profile.phone,
                body="[Rebound] Understood. No flights were booked. Contact support if you need further assistance.",
                sms_type="rejection_acknowledgement",
            )
            record = DecisionRecord(
                event_id=event_id,
                action=ActionType.ESCALATE,
                reasoning="Traveler declined rebooking options via SMS.",
            )
            self.db.record_run(self.run_id, event_id, record.action.value, trace_path=self.tracer.file_path)
            return record

        if clean_reply in ("1", "2"):
            idx = int(clean_reply) - 1
            chosen = ranked_offers[idx] if ranked_offers and idx < len(ranked_offers) else (ranked_offers[0] if ranked_offers else None)
            if not chosen:
                return self._handle_escalation(None, deadline or datetime.now(timezone.utc), "No valid offer mapped to reply.")

            self.db.resolve_pending_approval(event_id, status="approved")
            cost_delta = chosen.total_amount - self.original_order_total
            alts = [o.id for o in ranked_offers if o.id != chosen.id] if ranked_offers else []

            record = self._execute_booking_and_side_effects(
                event=original_event,
                deadline=deadline or chosen.arrives_at + timedelta(hours=self.profile.default_deadline_buffer_hours),
                chosen_offer=chosen,
                cost_delta=cost_delta,
                reasoning=f"Approved by traveler via SMS (Option {clean_reply}).",
                alternatives=alts,
                hold_order_id=hold_order_id,
            )
            # The booking helper has already written an audit row describing this as a
            # plain auto-book. Correct it, so the record and the database agree that a
            # human authorized this booking and which event it belongs to.
            record.action = ActionType.ASK_THEN_BOOK
            record.event_id = event_id
            self.db.record_run(
                run_id=self.run_id,
                event_id=event_id,
                action=record.action.value,
                chosen_offer_id=chosen.id,
                cost_delta_usd=cost_delta,
                trace_path=self.tracer.file_path,
            )
            return record

        # Timeout or unparseable response
        self.db.resolve_pending_approval(event_id, status="expired")
        return self._handle_escalation(None, deadline or datetime.now(timezone.utc), "Traveler approval timed out or reply was invalid.")

    # -------------------------------------------------------------------------
    # Irreversible Actions Sequence: Book -> Verify -> Cancel Old -> Notify
    # -------------------------------------------------------------------------
    def _execute_booking_and_side_effects(
        self,
        event: Optional[DisruptionEvent],
        deadline: datetime,
        chosen_offer: FlightOffer,
        cost_delta: float,
        reasoning: str,
        alternatives: List[str],
        hold_order_id: Optional[str] = None,
    ) -> DecisionRecord:
        """
        Executes irreversible actions strictly in safe order:
          1. Book new flight
          2. Verify new flight exists via Duffel GET
          3. Only after verification, cancel old flight
          4. Patch Google Calendar
          5. Send Gmail itinerary & draft EU261 compensation if eligible
          6. Alert pickup contacts
        """
        # Step 7: Act - Confirm Booking
        booking: Optional[ConfirmedBooking] = None
        with self.tracer.span("act", "duffel.orders.confirm", {"offer_id": chosen_offer.id}) as s:
            booking = self.duffel.confirm_booking(chosen_offer.id, self.profile, hold_order_id=hold_order_id)
            s["summary"] = f"Booked order {booking.order_id} (Ref: {booking.booking_reference})"

        # Step 8: Verify - Check order integrity
        with self.tracer.span("verify", "duffel.orders.get", {"order_id": booking.order_id}) as s:
            verified_order = None
            try:
                verified_order = self.duffel.get_order(booking.order_id)
            except Exception as e:
                logger.warning("Order verification check failed with exception: %s", e)
                verified_order = None

            # Duffel v2 orders don't expose a `status` string; the fake used
            # to. An order is considered live when it has a `booking_reference`
            # AND `cancelled_at` is falsy. We also accept the legacy status
            # values for the fake path.
            def _order_is_active(order: dict) -> bool:
                if not order:
                    return False
                legacy_status = order.get("status")
                if legacy_status in ("confirmed", "active"):
                    return True
                return bool(order.get("booking_reference")) and not order.get("cancelled_at")

            if not _order_is_active(verified_order):
                self.tracer.log_entry(
                    step="verify:failed",
                    tool="duffel.orders.get",
                    status=TraceStatus.ERROR,
                    output_summary=f"Verification mismatch on order {booking.order_id}. ABORTING old cancellation.",
                )
                return DecisionRecord(
                    event_id=event.event_id if event else "unknown",
                    action=ActionType.ESCALATE,
                    chosen_offer_id=chosen_offer.id,
                    reasoning="Order verification failed. Old flight was preserved.",
                )
            s["summary"] = f"Verified order {booking.order_id} is active."

        # Step 8b: Cancel Old Flight (Safe now that new ticket is confirmed)
        old_order_id = event.order_id if event and event.order_id else "ord_original"
        with self.tracer.span("cancel_old", "duffel.order_cancellations.create", {"old_order_id": old_order_id}) as s:
            try:
                quote = self.duffel.request_cancellation_quote(old_order_id, original_amount=self.original_order_total)
                self.duffel.confirm_cancellation(quote.cancellation_id)
                s["summary"] = f"Old order {old_order_id} cancelled. Refund quote: ${quote.refund_amount:.2f}"
            except Exception as e:
                logger.warning("Failed to cancel old booking %s: %s", old_order_id, e)
                s["summary"] = f"Cancellation error: {str(e)}"
                s["status"] = TraceStatus.RETRY

        # Step 8c: Google Calendar Update
        with self.tracer.span("calendar_update", "calendar.patch_trip_event", {"booking_ref": booking.booking_reference}) as s:
            try:
                self.calendar.patch_trip_event(
                    event_id="trip_flight_event",
                    new_start=chosen_offer.departs_at,
                    new_end=chosen_offer.arrives_at,
                    booking_ref=booking.booking_reference,
                )
                s["summary"] = f"Google Calendar flight card updated to {chosen_offer.departs_at.strftime('%H:%M')}"
            except Exception as e:
                logger.warning("Google Calendar patch failed: %s", e)
                s["summary"] = f"Calendar patch error (non-fatal): {e}"
                s["status"] = TraceStatus.RETRY

        # Step 8d: Gmail Itinerary & EU261 Compensation
        with self.tracer.span("email_dispatch", "gmail.send_itinerary_email", {"to": self.profile.email}) as s:
            itinerary_body = (
                f"Subject: [Rebound] Rebooked: {flight_code(chosen_offer.carrier, chosen_offer.flight_number)}\n\n"
                f"Your flight was disrupted. I have rebooked you:\n"
                f"  {flight_code(chosen_offer.carrier, chosen_offer.flight_number)} {chosen_offer.origin or 'LHR'} -> {chosen_offer.destination or 'JFK'}\n"
                f"  Departs: {chosen_offer.departs_at.strftime('%H:%M')} | Arrives: {chosen_offer.arrives_at.strftime('%H:%M')}\n"
                f"  Booking Reference: {booking.booking_reference} (Duffel: {booking.order_id})\n"
                f"  Cost Difference: +${cost_delta:.2f}\n\n"
                f"Calendar updated. Trip intact."
            )
            try:
                self.gmail.send_itinerary_email(self.profile.email, f"[Rebound] Rebooked: {chosen_offer.carrier}", itinerary_body)
                s["summary"] = "Itinerary email sent via Gmail."
            except Exception as e:
                logger.warning("Gmail dispatch failed: %s", e)
                s["summary"] = f"Gmail error (non-fatal): {e}"
                s["status"] = TraceStatus.RETRY

            # Check EU261 eligibility
            if event:
                eligible, reason = check_eu261_eligibility(event)
                if eligible:
                    try:
                        claim_body = f"Formal compensation claim under Regulation (EC) 261/2004 for {event.flight.number} on {event.flight.scheduled_departure.strftime('%Y-%m-%d')}."
                        self.gmail.create_compensation_draft(self.profile.email, f"EU261 Claim - {event.flight.number}", claim_body)
                        self.tracer.log_entry(step="claim:drafted", tool="gmail.draft", output_summary="EU261 compensation claim drafted.")
                    except Exception as e:
                        logger.warning("EU261 claim drafting error (non-fatal): %s", e)
                        self.tracer.log_entry(step="claim:error", tool="gmail.draft", status=TraceStatus.RETRY, output_summary=str(e))

        # Step 8e: Notify Pickup Contact
        # Non-critical: the ticket is already issued and verified by this point, so a
        # courtesy SMS that fails (unverified number on a Twilio trial, carrier
        # rejection) must never unwind a completed booking.
        if self.profile.contacts:
            pickup = self.profile.contacts[0]
            with self.tracer.span("pickup_notify", "twilio.send_sms", {"to": pickup.phone}) as s:
                try:
                    self.twilio.send_sms(
                        to_phone=pickup.phone,
                        body=f"[Rebound] ETA update for {self.profile.name}: New arrival is {chosen_offer.arrives_at.strftime('%H:%M')} on {flight_code(chosen_offer.carrier, chosen_offer.flight_number)}.",
                        sms_type="pickup_notification",
                    )
                    s["summary"] = f"Notified pickup contact {pickup.name} at {pickup.phone}"
                except Exception as e:
                    logger.warning("Pickup contact notification failed: %s", e)
                    s["summary"] = f"Pickup notification error (non-fatal): {e}"
                    s["status"] = TraceStatus.RETRY

        # Step 9: Report & Summary SMS
        # The traveler-facing summary is a nice-to-have; the flight is already
        # booked, verified, calendar-patched, and email-sent by this point.
        # A Twilio hiccup here (e.g. trial-account daily limit) must not crash
        # the run and mask a successful booking.
        with self.tracer.span("report", "twilio.send_sms", {"to": self.profile.phone}) as s:
            try:
                self.twilio.send_sms(
                    to_phone=self.profile.phone,
                    body=f"[Rebound] Rebooked on {flight_code(chosen_offer.carrier, chosen_offer.flight_number)} arriving {chosen_offer.arrives_at.strftime('%H:%M')}. Booking ref: {booking.booking_reference}. Itinerary emailed.",
                    sms_type="summary",
                )
                s["summary"] = "Final summary SMS dispatched."
            except Exception as e:
                logger.warning("Summary SMS failed but booking stands: %s", e)
                s["summary"] = f"Summary SMS error (non-fatal, booking preserved): {e}"
                s["status"] = TraceStatus.RETRY

        record = DecisionRecord(
            event_id=event.event_id if event else "evt_resumed",
            action=ActionType.BOOK,
            chosen_offer_id=chosen_offer.id,
            cost_delta_usd=cost_delta,
            arrives_at=chosen_offer.arrives_at,
            deadline=deadline,
            reasoning=reasoning,
            alternatives=alternatives,
        )

        self.db.record_run(
            run_id=self.run_id,
            event_id=record.event_id,
            action=record.action.value,
            chosen_offer_id=chosen_offer.id,
            cost_delta_usd=cost_delta,
            trace_path=self.tracer.file_path,
        )
        return record

    def _handle_escalation(
        self,
        event: Optional[DisruptionEvent],
        deadline: datetime,
        reason: str,
        best_found: Optional[FlightOffer] = None,
    ) -> DecisionRecord:
        """Dispatches escalation alerts to traveler when no safe autonomous rebooking is possible."""
        best_info = (
            f" Best option found was +${best_found.total_amount - self.original_order_total:.2f}."
            if best_found else ""
        )
        sms_body = (
            f"[Rebound] Could not automatically rebook: {reason}{best_info} "
            f"Nothing was booked or charged. Please contact your airline agent."
        )
        self.twilio.send_sms(to_phone=self.profile.phone, body=sms_body, sms_type="escalation")

        record = DecisionRecord(
            event_id=event.event_id if event else "unknown",
            action=ActionType.ESCALATE,
            deadline=deadline,
            reasoning=reason,
        )
        self.db.record_run(self.run_id, record.event_id, ActionType.ESCALATE.value, trace_path=self.tracer.file_path)
        return record

