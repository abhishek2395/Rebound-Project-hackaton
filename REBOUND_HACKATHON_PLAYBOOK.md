# REBOUND — Hackathon Playbook

**Event:** Lemma x Comma Capital Multi-App AI Agent Hackathon — Sunday Sep 13, 9AM–5PM, virtual
**Judges:** Phillip Li and Akira Tong (Arga Labs)
**Rubric:** Technical execution 30% · Reliability & evaluation 25% · Usefulness 20% · Originality 15% · Demo clarity 10%
**Submit:** Working repo · 2-minute demo video · Short system & reliability brief

> The brief literally says "Show how you know it works." 55% of the score is execution + reliability. We are building an **evaluation harness with an agent inside it**, not an agent with some tests bolted on.

---

## 0. One-liner and pitch

**Rebound** — when your flight is cancelled or badly delayed, the agent finds the best rebooking that fits your constraints and your calendar, asks you only when it has to, books it, fixes your calendar, notifies the people who care, and files your compensation claim.

**Pitch line:** "Rebound has already fixed your trip by the time the airline emails you about the cancellation."

**Why it wins on this rubric**
- Usefulness: everyone has lived this problem.
- Technical: a real multi-step loop with a genuine decision in the middle (rank + mandate check + approval).
- Reliability: fully sandboxable — we run 30 scenarios in CI and report numbers.
- Originality: nobody has shown the *agentic* version with guardrails and evals.

---

## 1. Morning checklist (do before 9AM or in the first 30 min)

### Accounts & keys
- [ ] **Duffel** account → test-mode API key (starts with `duffel_test_`). Do ONE manual booking end-to-end via API (offer request → offer → order) before writing agent code. Verify order cancellation works.
- [ ] **Google Cloud** project → OAuth client (Desktop app type) → enable Calendar API + Gmail API → download `credentials.json` → run the OAuth flow once locally to get `token.json`. Add your teammates' Gmail as test users on the consent screen (unverified app).
- [ ] **Twilio** trial → get a number → verify every teammate's phone (trial can only text verified numbers). Confirm one outbound SMS works. Inbound replies need a public URL: use `ngrok` (or Cloudflare tunnel).
- [ ] **Anthropic API key** (or whichever model provider) — set as env var.
- [ ] **GitHub** repo created, everyone has push access, CI (GitHub Actions) enabled.
- [ ] `ngrok` installed and authenticated.

### Env file (`.env.example`)
```
DUFFEL_API_KEY=
ANTHROPIC_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TRAVELER_PHONE=
GOOGLE_CREDENTIALS_PATH=./credentials.json
GOOGLE_TOKEN_PATH=./token.json
DRY_RUN=true
SPEND_CEILING_USD=800
APPROVAL_THRESHOLD_USD=300
```

### Decisions to lock in the first 15 minutes
- Language/framework: **Python 3.11 + FastAPI** (webhook receiver) + plain tool-use loop with the Anthropic SDK. No heavy agent framework — we need to control retries, idempotency and traces ourselves; that is the whole point.
- Model: a strong model for the ranking/decision step, a fast one for parsing/classification.
- Fixture format (Section 6) — agree on it before anyone writes code.
- Who owns what (Section 9).

---

## 2. Scope: MVP and the cut list

### MVP (must ship by 3:30PM)
1. Webhook receiver accepts a disruption event (we fire it ourselves with `curl`).
2. Agent loads traveler profile + calendar, searches Duffel, ranks options, applies mandate rules.
3. Within mandate → books via Duffel. Outside → SMS approval with top-2 options, waits for YES/NO reply.
4. On booking: update Calendar event, send itinerary email, log trace.
5. Idempotency: duplicate event ID → no second booking.
6. Eval harness: 30 JSON fixtures, `pytest` runs them, CI badge, results table auto-written to `EVAL_RESULTS.md`.
7. Trace viewer: single static HTML page that renders a run's JSON trace as a timeline.

### Nice-to-have (only after MVP is green)
- Compensation claim email (EU261 / DOT) when the delay qualifies.
- Notify pickup contact via SMS.
- Hotel late-arrival email.
- Nearby-airport search widening.
- LLM-as-judge score on ranking quality.

### Explicitly cut
- Real FlightAware/AeroAPI integration (paid, not needed — we simulate).
- Multi-passenger bookings.
- Seat selection, baggage.
- Any real money.
- Fancy dashboard beyond the trace viewer.

---

## 3. Architecture

```
[Disruption webhook]  ──►  FastAPI /webhook/disruption
                                 │  (idempotency check on event_id)
                                 ▼
                          ┌─────────────┐
                          │  Agent loop │  ◄── traveler profile (JSON)
                          └─────────────┘
        ┌──────────┬──────────┼──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼
     Duffel    Calendar    Gmail      Twilio    Trace log
   (search/   (read trip, (itinerary, (approval, (JSON per run)
    book/     update      claim,      notify)
    cancel)   event)      hotel)
```

### Agent loop (each step is a named node; every node writes a trace entry)
1. **ingest** — validate event, dedupe by `event_id` (store in SQLite `events` table). Duplicate → return 200, trace `skipped_duplicate`, stop.
2. **context** — load traveler profile; read Calendar for the trip event and the *first commitment at destination* (this defines the hard deadline).
3. **assess** — is action needed? A delay that still meets the deadline → notify only.
4. **search** — Duffel offer request: same origin/destination, departure window from now to deadline minus buffer. If empty and profile allows → widen to alternate airports (nice-to-have).
5. **rank** — deterministic filter (arrives before deadline, ≤ max layovers, cabin OK) → LLM ranks survivors on preference tradeoffs → output top-3 with reasons.
6. **decide** — mandate rules:
   - no viable options → `escalate`
   - best option cost delta ≤ approval threshold → `book`
   - cost delta > threshold but ≤ spend ceiling → `ask` (SMS, wait for reply, timeout 10 min → escalate)
   - > spend ceiling → `escalate` with summary
7. **act** — create Duffel order → cancel old order (or record change) → update Calendar → send email → notify contacts.
8. **verify** — GET the Duffel order, assert it matches decision; assert Calendar event updated. Mismatch → trace `verify_failed`, alert.
9. **report** — write trace file, summary SMS.

### Reliability mechanics (say these out loud in the video)
- **Idempotency:** `event_id` unique in SQLite; also `booking_intent_id` derived from `event_id` so a crash mid-act never double-books.
- **Retries:** all external calls wrapped in exponential backoff (3 tries, 1s/2s/4s) on 5xx/timeouts only. Never retry a POST order that returned 2xx.
- **Ordering of irreversible actions:** book new → verify → *then* cancel old. Never cancel before the new one is confirmed.
- **DRY_RUN mode:** all writes logged, none executed. Used in CI.
- **Timeouts:** approval wait 10 min; total run budget 5 min of tool time.
- **Guardrails are code, not prompts:** spend ceiling and deadline checks are Python assertions on the LLM's chosen option. The LLM proposes; code disposes.

---

## 4. Data models

### Traveler profile (`profiles/alex.json`)
```json
{
  "traveler_id": "alex",
  "name": "Alex Rivera",
  "email": "alex@example.com",
  "phone": "+1555...",
  "preferences": {
    "preferred_airlines": ["ZZ"],
    "max_layovers": 1,
    "seat": "aisle",
    "cabin": "economy",
    "avoid_redeye": true
  },
  "mandate": {
    "spend_ceiling_usd": 800,
    "approval_threshold_usd": 300,
    "auto_book": true
  },
  "contacts": [
    { "name": "Sam", "relation": "pickup", "phone": "+1555..." }
  ]
}
```

### Disruption event (webhook payload)
```json
{
  "event_id": "evt_001",
  "type": "cancelled",              // cancelled | delayed | missed_connection
  "order_id": "ord_xxx",            // Duffel order for the original booking
  "flight": {
    "carrier": "ZZ",
    "number": "ZZ123",
    "origin": "LHR",
    "destination": "JFK",
    "scheduled_departure": "2026-09-14T10:00:00Z",
    "scheduled_arrival": "2026-09-14T13:00:00Z"
  },
  "delay_minutes": null,
  "reason": "weather"
}
```

### Decision record (agent output)
```json
{
  "event_id": "evt_001",
  "action": "book",                 // book | ask | escalate | notify_only | skipped_duplicate
  "chosen_offer_id": "off_xxx",
  "cost_delta_usd": 142.50,
  "arrives_at": "2026-09-14T18:30:00Z",
  "deadline": "2026-09-15T09:00:00Z",
  "reasoning": "…",
  "alternatives": ["off_yyy", "off_zzz"]
}
```

### Trace entry (one per step, appended to `traces/<run_id>.jsonl`)
```json
{
  "run_id": "run_abc",
  "step": "search",
  "tool": "duffel.offer_requests.create",
  "input": {...},
  "output_summary": "12 offers",
  "latency_ms": 840,
  "tokens_in": 0,
  "tokens_out": 0,
  "cost_usd": 0.0,
  "status": "ok",                   // ok | retry | error | skipped
  "ts": "2026-09-13T15:02:11Z"
}
```

---

## 5. Integration notes

### Duffel (verify these in the first 20 min — API details may have shifted)
- Test mode: key starts `duffel_test_`. Test airline is **Duffel Airways (IATA `ZZ`)** with synthetic inventory that always returns offers.
- Flow: `POST /air/offer_requests` (slices + passengers) → read `offers` → `POST /air/orders` with `selected_offers`, passengers (name, DOB, gender, email, phone), `payments: [{type: "balance", amount, currency}]`.
- Cancel: `POST /air/order_cancellations` then `POST /air/order_cancellations/{id}/actions/confirm`.
- Header: `Duffel-Version` is required — grab the current version string from the docs.
- Offers expire in minutes: search → rank → book must be fast. If an offer is expired at booking time, re-search once and re-rank (trace it).
- Prices in test mode are deterministic-ish; use `total_amount` for cost delta vs. original order.
- To simulate "no options before deadline," set a deadline earlier than the earliest ZZ departure rather than expecting Duffel to return empty.

### Google Calendar
- Trip event has the original flight in title/description; store `duffel_order_id` in the event's `extendedProperties.private` so the agent can find it.
- "First commitment at destination" = first event on the destination day that isn't the flight. That is the deadline. Fallback: profile `default_deadline_buffer_hours`.
- Update = patch the flight event's start/end/summary; add a description line with the new booking ref.

### Gmail
- Send via `users.messages.send` with a MIME message. Plain text is fine. Itinerary template in Section 11.

### Twilio
- Outbound: `client.messages.create(to, from_, body)`.
- Inbound: FastAPI `POST /sms` receives form-encoded `From`, `Body`. Set the webhook URL on the Twilio number to your ngrok URL.
- Approval protocol: agent sends `"[Rebound] Reply 1 or 2 to book, or NO. Expires in 10 min."` Store pending approval keyed by `event_id`. Reply parsed → resumes the run.
- Trial accounts prefix messages with "Sent from your Twilio trial account" — fine.

---

## 6. Evaluation suite

### Fixture format (`evals/fixtures/*.json`)
```json
{
  "id": "S01_cancelled_easy",
  "description": "Cancelled, good same-day option within threshold",
  "profile": "alex",
  "calendar": {
    "deadline": "2026-09-15T09:00:00Z"
  },
  "event": { ...disruption event... },
  "mock_duffel": {
    "offers": [ {"id":"off_a","total_amount":"120.00","arrives_at":"2026-09-14T17:00:00Z","segments":1} ],
    "failures": []                 // e.g. ["500","500"] to inject
  },
  "sms_reply": null,               // "1" | "2" | "NO" | null (timeout)
  "expected": {
    "action": "book",
    "chosen_offer_id": "off_a",
    "bookings_created": 1,
    "calendar_updated": true,
    "emails_sent": ["itinerary"],
    "sms_sent": []
  }
}
```

### Mocking strategy
- In CI, Duffel/Calendar/Gmail/Twilio are replaced by in-memory fakes that record calls (`FakeDuffel`, etc.). The agent code is identical; only the client is swapped via dependency injection.
- One "live" smoke test (`pytest -m live`) hits real sandboxes; run manually before the video.

### The 30 scenarios
| # | ID | Situation | Expected action | What it proves |
|---|----|-----------|-----------------|----------------|
| 1 | S01_cancelled_easy | Cancelled; good option, delta $120 | book | happy path |
| 2 | S02_cancelled_over_threshold_yes | Best option delta $450; reply "1" | ask → book | approval flow |
| 3 | S03_cancelled_over_threshold_no | Same; reply "NO" | ask → escalate, 0 bookings | respects NO |
| 4 | S04_cancelled_over_threshold_timeout | Same; no reply | ask → escalate after timeout | timeout handling |
| 5 | S05_cancelled_over_ceiling | Only option delta $1,200 | escalate, 0 bookings | spend ceiling |
| 6 | S06_delay_still_makes_it | 3h delay, deadline tomorrow 9AM | notify_only | doesn't over-act |
| 7 | S07_delay_misses_deadline | 5h delay pushes arrival past deadline | book | delay → rebook |
| 8 | S08_missed_connection | Second leg missed; first flown | book second leg only | partial rebook |
| 9 | S09_no_options_before_deadline | All offers arrive after deadline | escalate | never books a bad flight |
| 10 | S10_duplicate_webhook | S01 event delivered twice | 1 booking, second run skipped_duplicate | idempotency |
| 11 | S11_duffel_500_twice | Offer request 500s twice then OK | book (with 2 retries in trace) | retry/backoff |
| 12 | S12_duffel_500_forever | Always 500 | escalate, 0 bookings | gives up cleanly |
| 13 | S13_offer_expired_at_booking | First booking attempt → offer expired | re-search, book | expiry recovery |
| 14 | S14_empty_results_widen | No offers LHR→JFK; offers LHR→EWR | book EWR (if widening on) else escalate | search widening |
| 15 | S15_layover_limit | Cheapest option has 2 layovers, max 1 | book the 1-layover option | hard filter |
| 16 | S16_redeye_avoid | Cheapest is 11PM departure, avoid_redeye | prefer non-redeye if within threshold | soft preference |
| 17 | S17_preferred_airline_tiebreak | Two equal options, one on ZZ | book ZZ | preference |
| 18 | S18_calendar_conflict | New arrival overlaps a meeting | book + flag conflict in SMS | calendar reasoning |
| 19 | S19_no_calendar_deadline | Calendar empty | use default buffer, book | fallback deadline |
| 20 | S20_crash_after_book_before_cancel | Simulated crash | on rerun: no second booking, old cancelled | crash safety |
| 21 | S21_verify_mismatch | Duffel returns different order than requested | verify_failed, alert, no cancel of old | verify step |
| 22 | S22_compensation_eligible | EU departure, cancellation <14 days notice | claim email drafted, cites EU261 | claim logic |
| 23 | S23_compensation_not_eligible | Weather-caused, US domestic | no claim email | doesn't spam |
| 24 | S24_pickup_contact_notified | Profile has pickup contact | SMS to contact with new ETA | contacts |
| 25 | S25_auto_book_off | mandate.auto_book=false, cheap option | ask, then book on "1" | mandate flag |
| 26 | S26_malformed_event | Missing origin | 400, no run | input validation |
| 27 | S27_calendar_api_down | Calendar 503 | book anyway, calendar step marked error, alert | degrades gracefully |
| 28 | S28_sms_api_down_when_ask_needed | Twilio fails on ask | escalate via email, 0 bookings | fallback channel |
| 29 | S29_gmail_down | Gmail 503 after booking | booking stands, email retried, error traced | non-critical failure |
| 30 | S30_cost_delta_negative | New option cheaper | book, note refund | edge math |

Target: ≥ 26/30 green by 3:30PM. Report *all* results, including reds, in `EVAL_RESULTS.md`.

### LLM-as-judge (nice-to-have)
For scenarios with ≥ 2 viable offers, a separate model call scores the chosen option 1–5 against the profile preferences with a one-line rationale. Report mean score. Keep this *separate* from pass/fail.

### CI
GitHub Actions: `pytest evals/ --junitxml` on every push; a script converts results to a markdown table and commits `EVAL_RESULTS.md`. Badge in README.

---

## 7. Decision policy (spell this out in the brief)

**Hard constraints (code):**
1. Arrival ≤ deadline − 60 min buffer.
2. Layovers ≤ `max_layovers`.
3. Cabin matches or is an upgrade.
4. Cost delta ≤ `spend_ceiling_usd` (else escalate).

**Routing (code):**
- 0 survivors → `escalate`
- delta ≤ `approval_threshold_usd` and `auto_book` → `book`
- otherwise → `ask` (top-2), reply `1|2` → book, `NO`/timeout → escalate

**Soft ranking (LLM, over survivors only):** earliest arrival, cost delta, preferred airline, avoid red-eye, fewer layovers. Output strict JSON: `{"ranked": ["off_a","off_b"], "reasons": {...}}`. Any offer ID not in the survivors list → rejected by code, re-prompt once, then fall back to earliest-arrival.

---

## 8. Hour-by-hour plan (9AM–5PM)

| Time | Everyone | Notes |
|------|----------|-------|
| 9:00–9:30 | Kickoff. Confirm accounts, keys, ngrok, repo skeleton, fixture format. Assign roles. | No agent code before Duffel manual booking succeeds. |
| 9:30–11:00 | Build in parallel (Section 9). | P2 ships real Duffel client + FakeDuffel first; everyone else codes against the fake. |
| 11:00 | **Checkpoint 1:** S01 passes end-to-end in DRY_RUN with fakes. | If not, cut nice-to-haves now. |
| 11:00–12:30 | Approval flow, idempotency, retries. Fixtures S01–S15 written. | |
| 12:30 | **Checkpoint 2:** S01–S12 green in CI. Live smoke test against real Duffel sandbox. | Lunch at keyboards. |
| 12:30–2:00 | Calendar + Gmail wired. Fixtures S16–S30. Trace viewer. | |
| 2:00 | **Checkpoint 3:** ≥ 22/30 green. Live end-to-end run with real SMS approval works. | Record a *backup* video now, even if rough. |
| 2:00–3:30 | Fix reds, nice-to-haves only if MVP is stable. Write brief + README. | |
| 3:30 | **Feature freeze.** | Nothing new. Only fixes. |
| 3:30–4:30 | Final live run recorded. Edit 2-minute video. Finalize brief. | |
| 4:30–5:00 | Submit. Buffer. | Submit by 4:45 at the latest. |

---

## 9. Team roles (4 people)

**P1 — Agent core**
`agent/loop.py`, `agent/policy.py`, `agent/prompts.py`. The step functions, decision rules, approval state machine, verify step. Owns the ranking prompt.

**P2 — Integrations**
`clients/duffel.py`, `clients/gcal.py`, `clients/gmail.py`, `clients/twilio.py`, plus `clients/fakes.py`. Retry wrapper. FastAPI `/webhook/disruption` and `/sms`. ngrok. Does the manual Duffel booking at 9:05.

**P3 — Evals**
`evals/fixtures/*.json`, `evals/test_scenarios.py`, `evals/runner.py`, CI workflow, `EVAL_RESULTS.md` generator, failure injection in fakes, LLM-judge (late). Writes the scenario table in the brief.

**P4 — Traces, video, brief**
`traces/` writer + `viewer/index.html` (timeline from JSONL), README, `RELIABILITY_BRIEF.md`, screen recording + editing, architecture diagram. Also runs the live smoke tests at each checkpoint.

If 3 people: P4's viewer goes to P3; P1 writes the brief. If 2 people: P1 = core + evals, P2 = integrations + video.

---

## 10. Repo structure

```
rebound/
├── README.md
├── RELIABILITY_BRIEF.md
├── EVAL_RESULTS.md          (auto-generated)
├── .env.example
├── .github/workflows/evals.yml
├── app/
│   ├── main.py              FastAPI: /webhook/disruption, /sms, /health
│   └── db.py                SQLite: events, bookings, pending_approvals
├── agent/
│   ├── loop.py              run(event) -> DecisionRecord
│   ├── policy.py            hard filters, routing, mandate checks
│   ├── prompts.py           ranking + classification prompts
│   └── models.py            pydantic models (Section 4)
├── clients/
│   ├── base.py              retry/backoff wrapper, timing
│   ├── duffel.py
│   ├── gcal.py
│   ├── gmail.py
│   ├── twilio_sms.py
│   └── fakes.py             recording fakes + failure injection
├── tracing/
│   └── tracer.py            JSONL writer, cost/latency capture
├── viewer/
│   └── index.html           drop a trace file in, see a timeline
├── evals/
│   ├── fixtures/S01…S30.json
│   ├── test_scenarios.py
│   └── report.py            junit -> EVAL_RESULTS.md
├── profiles/alex.json
└── scripts/
    ├── fire_event.sh        curl a disruption at the webhook
    ├── seed_calendar.py     create the trip + meeting events
    └── manual_booking.py    the 9:05 Duffel sanity check
```

---

## 11. Prompts and templates

### Ranking prompt (system)
```
You rank flight options for a traveler whose original flight was disrupted.
You will receive: traveler preferences, the hard deadline, and a list of
offers that ALREADY satisfy all hard constraints. Rank ALL of them.
Weigh, in order: arrival comfortably before the deadline, lower cost delta,
preferred airlines, avoiding red-eye departures (22:00–05:00 local) if
requested, fewer layovers. Do not invent offers. Respond with JSON only:
{"ranked": ["<offer_id>", ...], "reasons": {"<offer_id>": "<one sentence>"}}
```

### Approval SMS
```
[Rebound] Your ZZ123 LHR→JFK was cancelled. Options:
1) ZZ201 dep 14:10 arr 17:05, +$450, nonstop
2) ZZ305 dep 16:40 arr 20:30, +$310, nonstop
Reply 1 or 2 to book, or NO. Expires in 10 min.
```

### Escalation SMS
```
[Rebound] ZZ123 cancelled. I couldn't find a flight that arrives before
your 9:00 meeting within your $800 limit. Best I found: ZZ305 arr 20:30
(+$1,150). Nothing booked. Reply CALL and I'll draft the airline call notes.
```

### Itinerary email (plain text)
```
Subject: [Rebound] Rebooked: ZZ201 LHR→JFK, Sun 14 Sep

Your original flight ZZ123 was cancelled. I booked:
  ZZ201  LHR 14:10 → JFK 17:05  Nonstop  Economy
  Booking ref: ABC123  (Duffel order ord_xxx)
  Cost difference: +$450.00 (approved by you at 15:07)

Calendar updated. Sam has been told your new ETA.
Compensation: your cancellation appears eligible under EU261 — a claim
email has been drafted in your Gmail drafts.
```

### Compensation claim (draft only, nice-to-have)
Subject: EU261 compensation claim — ZZ123 on 14 Sep 2026, booking ABC123. Body: flight details, cancellation notice time, request for compensation per Regulation (EC) 261/2004, passenger details. Only when: EU departure or EU carrier, notice < 14 days, cause not extraordinary (not weather/ATC/strike).

---

## 12. The 2-minute video (script)

| Time | Screen | Voice |
|------|--------|-------|
| 0:00 | Title card | "Your flight just got cancelled. Rebound is the agent that fixes it before you've found the airline's phone number." |
| 0:12 | Terminal: `./scripts/fire_event.sh cancelled` | "A cancellation event hits the webhook." |
| 0:20 | Trace viewer, live | "It reads the calendar to find the meeting the flight was for — that's the real deadline. Searches Duffel. Filters on hard constraints in code. Ranks the survivors with the model." |
| 0:45 | Phone screen (record with QuickTime / screen mirror) | "Best option is over the approval threshold, so it asks. I reply 1." |
| 1:00 | Trace continues; Duffel dashboard shows order; Calendar event changes; Gmail inbox | "Books, verifies the order, *then* cancels the old one. Calendar and email update." |
| 1:20 | `EVAL_RESULTS.md` / CI | "How do we know it works? Thirty scenarios in CI: duplicate webhooks, API outages, expired offers, no viable flights. 27 pass." |
| 1:40 | The failing rows | "Here are the three that don't, and what we learned." |
| 1:52 | Architecture slide | "Multi-step, four apps, guardrails in code, every step traced. Rebound." |

Record the live run 2–3 times; use the cleanest. Keep the phone reply real.

---

## 13. Reliability brief (one page — `RELIABILITY_BRIEF.md`)

1. **What it does** (3 sentences) and the apps it touches.
2. **Architecture diagram** + the 9-step loop.
3. **Decision policy** — hard constraints vs. soft ranking, routing rules, mandate.
4. **Reliability mechanisms** — idempotency, retry policy, ordering of irreversible actions, verify step, dry-run, timeouts, degraded modes.
5. **Evaluation** — the scenario table with pass/fail, how mocks work, the live smoke test, LLM-judge score if done.
6. **Failures found during the build and how we fixed them** (keep a running list all day — this section is gold).
7. **Known limitations / what we'd do next** — real disruption feed, multi-passenger, richer calendar reasoning, Lemma-style monitoring in production.

---

## 14. Risks and fallbacks

| Risk | Fallback |
|------|----------|
| Duffel sandbox behaves unexpectedly | FakeDuffel is the primary path in CI anyway; video can use the fake with a caption if live fails — but try hard for live. |
| Google OAuth consent screen blocks teammates | Use one teammate's account for all live Google calls. |
| Twilio trial can't reach a number | Pre-verify all numbers at 9:00; fallback approval channel = email (S28 covers this). |
| ngrok URL changes | Paid/static domain or just re-paste the URL in Twilio when restarted. |
| Offers expire mid-run | S13 handles it; keep search→book under 60s. |
| LLM returns bad JSON | Strict parsing, one re-prompt, deterministic fallback (earliest arrival). Trace it. |
| Running out of time | Feature freeze 3:30 is non-negotiable. A 26/30 harness with an honest brief beats a 30/30 claim with no evidence. |

---

## 15. Submission checklist

- [ ] Repo public (or judges added), README with setup + `make demo` + CI badge
- [ ] `EVAL_RESULTS.md` auto-generated and current
- [ ] `RELIABILITY_BRIEF.md` complete, including the failures-found section
- [ ] Trace viewer works with a sample trace committed in `traces/sample_run.jsonl`
- [ ] 2-minute video uploaded (unlisted YouTube or Loom), link in README
- [ ] `.env.example` present, no real keys committed (grep for `duffel_test_` before pushing)
- [ ] Team names and contact in README
- [ ] Submitted before 4:45PM
