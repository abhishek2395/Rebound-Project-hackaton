# Rebound Evaluation Matrix & Reliability Results

**Last Run:** 2026-09-13 20:33:50 UTC  
**Test Suite:** 30 Chaos & Disruption Scenarios ($\\tau$-bench End-State Verification)  
**Pass Rate:** **30/30 (100.0%)**  

> **Reliability Directive:** Every scenario asserts the terminal state of the database, API fakes, 
> and irreversible side-effect ordering (Hold -> Verify -> Cancel Old).

---

## Summary Results Table

| # | Scenario ID | Description | Expected | Observed | Bookings | Retries | Latency | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | `S01_cancelled_easy` | Cancelled; good same-day option within threshold | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 2 | `S02_cancelled_over_threshold_yes` | Best option delta $450; reply '1' -> ask then book | `ask_then_book` | `ask_then_book` | 1 | 0 | 3ms | ✅ PASS |
| 3 | `S03_cancelled_over_threshold_no` | Over threshold; reply 'NO' -> escalate, 0 bookings | `escalate` | `escalate` | 0 | 0 | 2ms | ✅ PASS |
| 4 | `S04_cancelled_over_threshold_timeout` | Over threshold; no reply -> timeout and escalate | `escalate` | `escalate` | 0 | 0 | 3ms | ✅ PASS |
| 5 | `S05_cancelled_over_ceiling` | Only option delta $1,200 -> escalate over ceiling | `escalate` | `escalate` | 0 | 0 | 1ms | ✅ PASS |
| 6 | `S06_delay_still_makes_it` | 3h delay, deadline tomorrow 9AM -> notify_only | `notify_only` | `notify_only` | 0 | 0 | 9ms | ✅ PASS |
| 7 | `S07_delay_misses_deadline` | 5h delay pushes past deadline -> rebook | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 8 | `S08_missed_connection` | Second leg missed; rebook remaining journey | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 9 | `S09_no_options_before_deadline` | All offers arrive after deadline -> escalate | `escalate` | `escalate` | 0 | 0 | 2ms | ✅ PASS |
| 10 | `S10_duplicate_webhook` | Event delivered twice; 1 booking, second run skipped_duplicate | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 11 | `S11_duffel_500_twice` | Offer request 500s twice then succeeds with backoff | `book` | `book` | 1 | 2 | 2ms | ✅ PASS |
| 12 | `S12_duffel_500_forever` | Duffel fails continuously -> escalate cleanly | `escalate` | `escalate` | 0 | 3 | 1ms | ✅ PASS |
| 13 | `S13_offer_expired_at_booking` | Hold order locks inventory; prevents offer_expired failure | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 14 | `S14_empty_results_widen` | No direct options -> escalate with clear summary | `escalate` | `escalate` | 0 | 0 | 1ms | ✅ PASS |
| 15 | `S15_layover_limit` | Prunes 2-layover option; books 1-layover option | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 16 | `S16_redeye_avoid` | Prefers daytime flight over red-eye per profile preference | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 17 | `S17_preferred_airline_tiebreak` | Two equal options, breaks tie using preferred carrier ZZ | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 18 | `S18_calendar_conflict` | Books viable flight within deadline buffer | `book` | `book` | 1 | 0 | 10ms | ✅ PASS |
| 19 | `S19_no_calendar_deadline` | Empty calendar -> falls back to default 4h buffer | `book` | `book` | 1 | 0 | 1ms | ✅ PASS |
| 20 | `S20_crash_after_book_before_cancel` | Safe ordering ensures old flight is cancelled after verification | `book` | `book` | 1 | 0 | 1ms | ✅ PASS |
| 21 | `S21_verify_mismatch` | Simulated verify mismatch -> aborts cancellation of old order | `escalate` | `escalate` | 1 | 1 | 1ms | ✅ PASS |
| 22 | `S22_compensation_eligible` | EU departure operational cancellation -> draft EU261 claim | `book` | `book` | 1 | 0 | 1ms | ✅ PASS |
| 23 | `S23_compensation_not_eligible` | Weather disruption is exempt from EU261 compensation | `book` | `book` | 1 | 0 | 1ms | ✅ PASS |
| 24 | `S24_pickup_contact_notified` | Profile has pickup contact -> sends ETA update SMS to Sam | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |
| 25 | `S25_auto_book_off` | mandate.auto_book=false -> asks even if within threshold | `ask_then_book` | `ask_then_book` | 1 | 0 | 3ms | ✅ PASS |
| 26 | `S26_malformed_event` | Missing origin -> rejected by validation | `validation_error` | `validation_error` | 0 | 0 | 1ms | ✅ PASS |
| 27 | `S27_calendar_api_down` | Google Calendar 503 -> degrades gracefully, books using fallback buffer | `book` | `book` | 1 | 2 | 2ms | ✅ PASS |
| 28 | `S28_sms_api_down_when_ask_needed` | Twilio 500 on ask -> escalates via email | `escalate` | `escalate` | 0 | 0 | 1ms | ✅ PASS |
| 29 | `S29_gmail_down` | Gmail 503 after booking -> booking stands, error traced | `book` | `book` | 1 | 0 | 1ms | ✅ PASS |
| 30 | `S30_cost_delta_negative` | New flight cheaper -> books, calculates negative delta | `book` | `book` | 1 | 0 | 2ms | ✅ PASS |

---

## Key Invariants Empirically Proven

1. **Idempotency (S10):** Duplicate webhooks never trigger a second ticket order.
2. **Irreversible Action Order (S20, S21):** Old tickets are only cancelled *after* the new order is verified via Duffel GET. Verification mismatches abort the cancellation.
3. **Hold-Order Pattern (S02, S13):** Inventory is held before SMS approval, preventing 15-minute offer expiry.
4. **API Fault Tolerance (S11, S27, S29):** Network 500s trigger exponential backoff (1s, 2s, 4s). Non-critical calendar and email outages degrade gracefully.
5. **Spend Ceilings & Guardrails (S05, S15):** Policy limits exist in deterministic Python assertions; the model cannot book out-of-policy flights.

```
TOTAL SCENARIOS: 30 | PASSED: 30 | FAILED: 0
```
