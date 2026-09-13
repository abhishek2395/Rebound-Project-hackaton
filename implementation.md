# Implementation Plan: Rebound Multi-App AI Agent ($10,000 First-Place Blueprint)

**Target Event:** Lemma x Comma Capital Multi-App AI Agent Hackathon  
**Judges:** Akira Tong & Phillip Li (Arga Labs), Userlens founders  
**Rubric:** Technical Execution (30%) · Reliability & Evaluation (25%) · Usefulness (20%) · Originality (15%) · Demo Clarity (10%)  
**Core Directive:** Build a 4-app autonomous agent (Duffel, Google Calendar, Gmail, Twilio) backed by a 30-scenario evaluation harness with end-state verification.

---

## 2-Member Execution Strategy

* **Member A (Core Engine & Evals):** Pydantic models, LangGraph state machine, policy guardrails, `FakeDuffel`/Fakes, and the 30 pytest scenarios.
* **Member B (Integrations, Gateway & Demo):** FastAPI endpoints, live clients (Duffel, GCal, Gmail, Twilio), ngrok setup, live video demo, and `RELIABILITY_BRIEF.md`.

---

## Architecture & Engineering Mitigations

```mermaid
flowchart TD
    subgraph Ingress
        WH[External Disruption Webhook] --> API["FastAPI /webhook/disruption\n(HMAC Validation + Idempotency Check)"]
        API -->|Enqueue Background Task\nReturn 200 OK in <100ms| BG[Async Worker]
    end

    subgraph StateMachine["LangGraph State Machine (SqliteSaver)"]
        BG --> N_INGEST[1. ingest: Dedupe event_id]
        N_INGEST --> N_CONTEXT[2. context: Fetch Profile & GCal Destination Deadline]
        N_CONTEXT --> N_ASSESS[3. assess: Determine if flight meets deadline]
        N_ASSESS --> N_SEARCH[4. search: Duffel Offer Request]
        N_SEARCH --> N_RANK[5. rank: Code Filter -> LLM Soft Rank]
        N_RANK --> N_DECIDE{6. decide: Mandate Policy Check}
        
        N_DECIDE -->|Cost Delta <= $300| N_AUTO[Auto-Book Path]
        N_DECIDE -->|Cost Delta > $300 & <= $800| N_HOLD[Hold Order Pattern]
        N_DECIDE -->|Cost Delta > $800 or 0 Options| N_ESCALATE[Escalate Path]
        
        N_HOLD --> N_SMS_INTERRUPT["interrupt(action=sms_approval)\nSend Twilio SMS"]
    end

    subgraph HITL_Resume["SMS Resumption Loop"]
        TW_SMS[User Replies '1', '2', or 'NO'] --> SMS_API["FastAPI /sms (Twilio Webhook)"]
        SMS_API -->|Command resume=reply| N_ACT
    end

    subgraph Execution["Irreversible Action Sequence"]
        N_AUTO --> N_ACT[7. act: Confirm Payment / Ticket]
        N_SMS_INTERRUPT -.-> N_ACT
        N_ACT --> N_VERIFY{8. verify: Duffel GET Order Check}
        N_VERIFY -->|Matched| N_CANCEL[Cancel Old Flight: Quote -> Confirm]
        N_VERIFY -->|Mismatch| N_FAIL_ABORT[Alert Human & ABORT Old Cancellation]
        N_CANCEL --> N_SIDE_EFFECTS[Patch Google Calendar + Send Gmail Itinerary]
        N_SIDE_EFFECTS --> N_REPORT[9. report: Emit JSONL Trace + Summary SMS]
    end
```

### Critical Engineering Safeguards
1. **Twilio 5s Timeout Mitigation:** Webhooks acknowledge `200 OK` within 100ms and hand off execution to `BackgroundTasks`, eliminating duplicate delivery storms.
2. **Offer Expiration (Hold-Order Pattern):** Over-threshold flights are created as `hold` orders via `POST /air/orders` (without payment), locking the price for 15–30 minutes while awaiting the SMS reply.
3. **Irreversible Action Invariant:** `Book New` $\to$ `Verify New` $\to$ `Cancel Old`. Never cancel an existing ticket until the replacement is confirmed via a separate `GET /air/orders/{id}` check.
4. **Guardrails in Code:** Spend ceilings, layover limits, and calendar buffer calculations are Python assertions; the LLM ranks options but cannot violate constraints.
5. **SQLite Checkpointer CVE Mitigations:** Pin `langgraph>=1.0.10` and `langgraph-checkpoint-sqlite>=3.0.1` to block SQL injection and unsafe deserialization vulnerabilities.

---

## Phased Execution Plan

### Phase 1: Core Engine & Data Models
* `agent/models.py`: Pydantic schemas for `DisruptionEvent`, `TravelerProfile`, `Offer`, `HoldOrder`, `DecisionRecord`, and `TraceEntry`.
* `agent/policy.py`: Pure Python filter functions (`filter_survivors`, `evaluate_mandate`, `calculate_deadline_buffer`, `check_eu261_eligibility`).
* `agent/prompts.py`: Strict JSON-only LLM ranking prompt over pre-filtered survivors, with fallback deterministic sorter.
* `profiles/alex.json`: Baseline traveler profile.

### Phase 2: Dual-Mode Clients & Recording Fakes
* `clients/base.py`: Base client with latency tracking and `tenacity` exponential backoff (3 attempts on 5xx/429; 130s supplier timeout).
* `clients/fakes.py`: `FakeDuffel`, `FakeCalendar`, `FakeGmail`, `FakeTwilio` supporting failure injection, parametric traps, and call recording.
* `clients/duffel.py`: Live Duffel API client for test sandbox (`duffel_test_` key, IATA `ZZ`).
* `clients/gcal.py`: Google Calendar OAuth2 client for reading destination commitments and patching events.
* `clients/gmail.py`: Gmail client for sending itineraries and drafting EU261 claims.
* `clients/twilio_sms.py`: Twilio SMS client for approval requests and pickup contact notifications.

### Phase 3: LangGraph State Machine & Webhook Ingress
* `agent/loop.py`: LangGraph DAG with `SqliteSaver`, `interrupt()`, and `Command(resume=...)`.
* `tracing/tracer.py`: JSONL trace writer logging node, tool, input, output, latency, and status.
* `app/db.py`: SQLite schema for `events` (idempotency dedupe), `bookings`, and `pending_approvals`.
* `app/main.py`: FastAPI server with `POST /webhook/disruption`, `POST /sms`, `GET /health`, and `GET /api/runs`.
* **Checkpoint 1:** Scenario `S01` and `S02` pass end-to-end with fakes.

### Phase 4: Full 30-Scenario Evaluation Suite
* `evals/fixtures/`: 30 scenario JSON files (`S01.json` through `S30.json`) covering happy paths, approvals, cancellations, API failures, and edge cases.
* `evals/test_scenarios.py`: Pytest suite executing all 30 scenarios via τ-bench end-state verification.
* `evals/report.py`: Script parsing pytest output and generating `EVAL_RESULTS.md`.
* **Checkpoint 2:** $\ge 27/30$ scenarios green in CI.

### Phase 5: Next-Level Interactive Visualizer
* `viewer/index.html`:
  * **Interactive State Machine DAG:** Visual node highlighting as steps execute in real time.
  * **Multi-App Live Status Panel:** Real-time request/response cards for Duffel, Google Calendar, Gmail, and Twilio.
  * **Interactive SMS Approver:** Virtual phone UI rendering inbound approval texts with one-click reply simulation ("1", "2", "NO").
  * **Scenario Browser & Trace Timeline:** Filterable 30-scenario runner showing latency, retry traces, and diffs.
  * Cyber-refined dark-mode aesthetic with glassmorphism and Lucide icons.

### Phase 6: Live Smoke Test, Demo Video & Submission Deliverables
* `scripts/fire_event.sh`: Quick CLI to trigger disruption webhooks via curl.
* `scripts/seed_calendar.py`: Seeds test trip and destination meetings on Google Calendar.
* `RELIABILITY_BRIEF.md`: 1-page document detailing architecture, 9-step state machine, decision policy, failure handling, and post-mortems.
* `README.md`: Setup instructions, architecture diagram, CI badge, demo video link.
* **Checkpoint 3:** Record live end-to-end run (phone SMS $\to$ Duffel test booking $\to$ GCal patch $\to$ Gmail itinerary) and cut 2-minute demo video.

---

## Verification Plan

### Automated Tests
```bash
pytest evals/test_scenarios.py -v --junitxml=evals/junit.xml
python evals/report.py
```
* Verifies 100% of critical safety invariants (zero double-bookings, old flight never cancelled if verification fails, spend ceiling enforced).

### Live Demo Flow
1. Seed calendar with `python scripts/seed_calendar.py`.
2. Fire disruption webhook: `./scripts/fire_event.sh cancelled`.
3. Receive real SMS on phone, reply "1".
4. Real-time visualizer tracks DAG progress; Google Calendar patches time; Gmail receives itinerary; Duffel verifies ticket issuance.

