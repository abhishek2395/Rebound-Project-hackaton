# ✈️ Rebound — Autonomous Travel Disruption Recovery Agent

[![CI](https://github.com/abhishek2395/Rebound-Project-hackaton/actions/workflows/evals.yml/badge.svg?branch=main)](https://github.com/abhishek2395/Rebound-Project-hackaton/actions/workflows/evals.yml)
[![Evaluation Suite](https://img.shields.io/badge/Evaluations-30%2F30%20Passing%20(100%25)-brightgreen?style=for-the-badge&logo=checkmarx)](EVAL_RESULTS.md)
[![Apps Connected](https://img.shields.io/badge/Apps-Duffel%20%7C%20Google%20Calendar%20%7C%20Gmail%20%7C%20Twilio-0284c7?style=for-the-badge)](RELIABILITY_BRIEF.md)
[![Architecture](https://img.shields.io/badge/Architecture-9--Step%20State%20Machine%20DAG-10b981?style=for-the-badge)](RELIABILITY_BRIEF.md)
[![Live Duffel Sandbox](https://img.shields.io/badge/Duffel%20Sandbox-Verified-blueviolet?style=for-the-badge)](RELIABILITY_BRIEF.md#live-sandbox-verification)

> **"Rebound has already fixed your trip by the time the airline emails you about the cancellation."**

Built for the **Multi-App AI Agent Hackathon** (hosted by Lemma & Comma Capital, judged by Akira Tong & Phillip Li of *Arga Labs*, and the founders of *Userlens*).

---

## 📑 Key Documents for Judges
- 🛡️ [**`RELIABILITY_BRIEF.md`**](RELIABILITY_BRIEF.md) — Architecture deep-dive, decision policy, safety invariants, fault tolerance matrix, and post-mortems.
- 📊 [**`EVAL_RESULTS.md`**](EVAL_RESULTS.md) — Complete 30-scenario evaluation matrix with $\tau$-bench end-state verification and latency profiling.
- 🎨 [**`viewer/index.html`**](viewer/index.html) — Live cyber-refined 9-step DAG visualizer, 4-app status cards, and interactive smartphone SMS simulator.

---

## 🎯 The Problem

When an airline flight is cancelled or delayed, travelers face an operational nightmare:
1. **Losing Crucial Meetings:** A 3-hour delay might seem manageable, but if you have a keynote or board meeting 90 minutes after landing, your trip is already ruined.
2. **The 15-Minute Offer Expiry Race:** Alternative airline seats sell out within minutes. By the time a human traveler checks an alert and replies, the offer has expired (`offer_expired`).
3. **Double-Booking & Stranding:** Cancelling an existing flight before verifying that the replacement ticket was actually issued leaves the passenger stranded with nothing if the second booking fails.

---

## ⚡ What Rebound Does Across 4 Apps

```
                     ┌────────────────────────────────────────────────────────┐
                     │            Incoming Disruption Webhook                 │
                     └───────────────────────────┬────────────────────────────┘
                                                 │ (FastAPI <50ms ACK)
                                                 ▼
                     ┌────────────────────────────────────────────────────────┐
                     │          1. Ingest & SQLite WAL Deduplication          │
                     └───────────────────────────┬────────────────────────────┘
                                                 │
          ┌──────────────────────────────────────┼──────────────────────────────────────┐
          ▼                                      ▼                                      ▼
┌───────────────────┐                  ┌───────────────────┐                  ┌───────────────────┐
│  Google Calendar  │                  │ Duffel NDC Flight │                  │    Twilio SMS     │
│ commitment lookup │                  │ search & hold seat│                  │ approval gateway  │
└─────────┬─────────┘                  └─────────┬─────────┘                  └─────────┬─────────┘
          │                                      │                                      │
          └──────────────────────────────────────┼──────────────────────────────────────┘
                                                 │
                                                 ▼
                     ┌────────────────────────────────────────────────────────┐
                     │   Deterministic Mandate Evaluation (Python Policy)     │
                     │   - Delta <= $300: Auto-Book                           │
                     │   - Delta $301-$800: Hold-Order + SMS Approval         │
                     │   - Delta > $800: Clean Escalation (0 Bookings)        │
                     └───────────────────────────┬────────────────────────────┘
                                                 │
                                                 ▼
                     ┌────────────────────────────────────────────────────────┐
                     │      Irreversible Action Ordering (Book -> Verify)     │
                     │      1. Duffel Confirm Booking                         │
                     │      2. Duffel GET Order Verification                  │
                     │      3. Safe Cancellation of Old Ticket                │
                     └───────────────────────────┬────────────────────────────┘
                                                 │
          ┌──────────────────────────────────────┼──────────────────────────────────────┐
          ▼                                      ▼                                      ▼
┌───────────────────┐                  ┌───────────────────┐                  ┌───────────────────┐
│  Google Calendar  │                  │       Gmail       │                  │  Pickup Contact   │
│  flight card patch│                  │ itinerary + EU261 │                  │ ETA notification  │
└───────────────────┘                  └───────────────────┘                  └───────────────────┘
```

---

## 🛡️ Core Production Invariants

1. **"LLM Proposes, Deterministic Code Disposes":**
   LLMs are used exclusively for semantic ranking and human-facing message synthesis. Hard constraints (deadlines, layover limits, spend ceilings) are guarded by deterministic Python code.
2. **The Hold-Order Pattern (Zero Expiry Failures):**
   When human approval is needed via SMS, Rebound places a Duffel Hold Order before sending the text, locking the seat and freezing the price for up to 24 hours.
3. **Strict Irreversible Ordering (`Book` $\to$ `Verify` $\to$ `Cancel Old`):**
   Old tickets are only cancelled after verifying the new ticket exists via Duffel `GET`. If verification fails, cancellation is aborted, preserving the traveler's original flight.
4. **Idempotent Ingress & Sub-50ms ACK:**
   Disruption webhooks are acknowledged immediately to prevent retry floods. Duplicate webhook deliveries are deduplicated via SQLite WAL.

---

## 🚀 Quickstart & Demo Walkthrough

### 1. Setup Environment
```bash
# Clone repository
git clone https://github.com/abhishek2395/Rebound-Project-hackaton.git
cd Rebound-Project-hackaton

# Install dependencies (Python 3.11 recommended)
pip install -r requirements.txt

# Copy environment variables (supports live keys or built-in test sandbox)
cp .env.example .env
```

### 2. Run the 30-Scenario Evaluation Harness
Verify all 30 chaos, disruption, and failure scenarios in under 1 second:
```bash
python3 evals/test_scenarios.py
```
*Expected output:*
```
Ran 30 tests in 0.273s
OK
Generated EVAL_RESULTS.md with 30 results.
```

### 3. Launch the Server & Visualizer
```bash
uvicorn app.main:app --reload --port 8000
```
Open your browser to:
👉 **`http://localhost:8000/viewer/`**

### 4. Fire a Test Disruption Event
In a separate terminal, run our interactive trigger CLI:
```bash
./scripts/fire_event.sh
```
Choose from the interactive menu:
- **`1`**: Auto-book scenario (Delta $\le \$300$)
- **`2`**: Twilio SMS approval scenario (Delta $\$450$ $\to$ Watch the phone emulator update!)
- **`3`**: Minor delay (Arrives well before meeting $\to$ Notify only)
- **`4`**: Major delay (Misses destination commitment $\to$ Auto-rebook)
- **`5`**: Duplicate webhook storm (Verifies idempotency in real-time)
- **`6`**: High spend ceiling breach (Escalates without booking)

### 5. Interactive Smartphone Simulator
On the visualizer dashboard (`/viewer/`), test the SMS flow directly:
- Click **"Reply 1"** on the virtual phone to approve the rebooking.
- Watch Duffel confirm the ticket, Google Calendar update, Gmail send the itinerary, and EU261 compensation drafted!

---

## 🧪 Integrated Applications Overview

| Application | Protocol / SDK | Role in Rebound | Production Guardrail |
|---|---|---|---|
| **Duffel Flights API** | REST NDC API / SDK | Search alternative flights, place Hold Orders, confirm booking, verify order status, cancel old ticket | 130s supplier timeout handling, `tenacity` exponential backoff on 500s/429s, hold-order price lock |
| **Google Calendar** | Google OAuth2 REST | Fetch destination commitment deadline, patch flight itinerary card | Graceful degradation if Calendar API is down (uses fallback buffer) |
| **Gmail API** | Google Workspace REST | Dispatch HTML traveler itinerary, draft formal EU261 compensation claim | Automatic EC 261/2004 eligibility detection; drafts stay in drafts folder |
| **Twilio SMS Gateway** | Twilio REST Webhooks | Send human-in-the-loop approval requests, receive traveler reply, alert pickup contact | Form-encoded webhook parsing, 1-click option buttons, email fallback |

---

## 📊 Summary of Evaluation Results

All 30 scenarios run automated $\tau$-bench end-state assertions verifying the database, API fakes, and irreversible side-effect ordering:

```
================================================================================
TOTAL SCENARIOS: 30 | PASSED: 30 | FAILED: 0 | PASS RATE: 100.0%
AVERAGE EXECUTION LATENCY: 2.4ms (in-memory fakes) — live sandbox latency dominated by Duffel round-trip
================================================================================
```

For the complete breakdown of every scenario, see [**`EVAL_RESULTS.md`**](EVAL_RESULTS.md).

---

## 👥 Team
- **Abhishek Jaiswal** — agent policy, evals, reliability brief
- **Yash Jaiswal** — integrations, viewer, demo
- Multi-App AI Agent Hackathon — San Francisco, 2026
