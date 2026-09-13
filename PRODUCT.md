# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, both watching rather than operating:

1. **Hackathon judges** (Akira Tong and Phillip Li of Arga Labs, plus Userlens founders) evaluating the Lemma × Comma Capital Multi-App AI Agent Hackathon against a published rubric: technical execution 30%, reliability and evaluation 25%, usefulness 20%, originality 15%, demo clarity 10%. They see this surface primarily through a 2-minute demo video, and may open the repo afterward.
2. **The build team** (Abhishek Jaiswal, Yash Jaiswal) driving live demo runs from a laptop.

The surface is a spectator instrument, not a daily operator console. Nobody administers a fleet with it; its job is to make one agent run legible while it happens.

## Product Purpose

Rebound is an autonomous travel-disruption recovery agent. When a flight is cancelled or delayed, it reads the traveler's calendar to find the real deadline (the first commitment at the destination), searches Duffel for alternatives, filters them against hard constraints in deterministic Python, holds a seat, asks the traveler for approval by message when the fare delta exceeds their mandate, books, verifies the booking, and only then cancels the original ticket.

`viewer/index.html` is the window onto a single run of that agent. Success is a judge watching the demo and understanding, without narration, what the agent decided and why it is trustworthy.

## Positioning

The claim a neighbouring project cannot truthfully copy: **the guardrails are code, not prompts, and the evidence is real.** No model call sits anywhere in the decision path — ranking is a deterministic, auditable heuristic, so spend decisions never depend on LLM judgment. Two of four integrations are verified against live APIs (Duffel order `3ZPLMP`; a real approval message answered on a real handset and resumed by a separate process), and the two that are not are stated as such in the brief.

## Operating Context

- Driven live during a screen recording: a scenario is fired by `curl` or by a button in this surface, and the run unfolds over seconds while the camera rolls.
- The signature moment is a pause: at step 6 the agent stops, messages a human, and waits. A real person replies `1` on their phone. A separate process picks the run back up from SQLite and finishes it.
- Backed by a FastAPI gateway on `localhost`, reachable at `/api/runs`, `/api/traces/{run_id}`, `/api/demo/trigger?scenario=<id>`, and `/sms`.
- Judged partly from a recording, so legibility at video compression and at a glance outranks information density.

## Capabilities and Constraints

- Nine named steps: ingest, context, assess, search, rank, decide, act, verify, report. Steps emit JSONL trace entries carrying `step`, `tool`, `output_summary`, `latency_ms`, and `status` (`ok` / `retry` / `error` / `skipped`).
- Four integrations: Duffel (flights), Google Calendar (deadline), Gmail (itinerary, EU261 claim), Twilio (approval message, SMS or WhatsApp).
- Five terminal actions: `book`, `ask_then_book`, `ask`, `escalate`, `notify_only`, plus `skipped_duplicate`.
- A run can pause indefinitely at `decide:ask` awaiting a human, then resume in a different process.
- 30 evaluation scenarios (S01–S30) covering chaos, outages, retries, idempotency, and spend ceilings; all currently pass.
- Static single file served by FastAPI at `/viewer/`. No build step, no bundler, no framework. Loads Tailwind and Lucide from CDNs today.
- Terminology is fixed and must not be softened: hold order, cost delta, spend ceiling, approval threshold, mandate, escalate, idempotency, survivors.

## Brand Commitments

- Name: **Rebound**. Tagline in use: "Rebound has already fixed your trip by the time the airline emails you about the cancellation."
- Pinned by the user for this surface: an **airport departure-board** visual world. Chosen deliberately over a mission-control telemetry treatment and over refining the incumbent dark-slate dashboard.
- Repository already ships an architecture SVG (`docs/architecture.svg`) and live-proof screenshot (`docs/duffel_live.png`) in a separate, more conventional documentation register; this surface is not required to match them.

## Evidence on Hand

- `traces/sample_run.jsonl` — a real 16-entry trace from a live run including the human-approval pause and resume. This is the surface's default content and is genuine, not synthetic.
- `EVAL_RESULTS.md` — auto-generated 30-scenario matrix with per-scenario latency and retry counts.
- `docs/duffel_live.png` — Duffel dashboard showing order `3ZPLMP` created and cancelled against the real test API.
- Live Twilio/WhatsApp round trip confirmed (message status `read`, reply routed through the production webhook).
- **Absent, must not be fabricated:** any live Google Calendar or Gmail evidence. Those two integrations run against fakes only, and the brief says so explicitly.

## Product Principles

1. **Show the decision, not just the activity.** A progress bar proves nothing; the surface exists to expose what was chosen and which rule forced it.
2. **The pause is the product.** The moment the agent stops and defers to a human is the most important state, and must be the most visually arresting one.
3. **Claims are attached to evidence.** Anything asserted here is traceable to a trace entry, a scenario id, or a live API artifact.
4. **Failure states are first-class.** Escalations, retries, and verification aborts are proof of reliability, not embarrassments to hide.
5. **Legible on camera first.** If it cannot be read in a compressed 2-minute video at a glance, it is decoration.

## Accessibility & Inclusion

State must never be carried by color alone — every status needs a glyph or a word, since the surface is judged through video compression and by viewers who may not distinguish the amber/green/red signal set.
