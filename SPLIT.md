# Rebound — 2-3 hour split (Abhishek ↔ Yash)

Repo is live at https://github.com/abhishek2395/Rebound-Project-hackaton (30/30 evals passing against fakes, CI on, deps + Makefile in). Everything below runs off `main` — small commits, PR if you touch each other's area.

Hard deadline: **3:30 PM feature freeze**, submit by **4:45 PM**.

---

## Abhishek (you)

**A1. Duffel live sandbox smoke test — 30 min**
- Sign up at duffel.com → get `duffel_test_` API key.
- `cp .env.example .env`, fill `DUFFEL_API_KEY`.
- Write `scripts/manual_booking.py` if not present, or run the existing one — hit `POST /air/offer_requests`, pick a `ZZ` offer, `POST /air/orders`, then `POST /air/order_cancellations`. Confirm all four calls return 2xx.
- Screenshot the Duffel dashboard order row → save to `docs/duffel_live.png`.
- **Deliverable:** commit the screenshot + a line in RELIABILITY_BRIEF confirming live sandbox works.

**A2. EVAL_RESULTS + README audit — 20 min**
- Run `make test` locally on Python 3.11. Confirm 30/30 still passes; regenerate EVAL_RESULTS.md.
- Read README end-to-end; fix any claim that isn't backed by code (e.g. any latency number, any "we did X" that we didn't).
- Add the CI badge URL for the new workflow: `[![CI](https://github.com/abhishek2395/Rebound-Project-hackaton/actions/workflows/evals.yml/badge.svg)](...)`.

**A3. Record the 2-minute demo video — 45 min**
- Script is in `REBOUND_HACKATHON_PLAYBOOK.md` Section 12 — follow it beat for beat.
- Record 2-3 takes with QuickTime; keep the cleanest.
- Upload unlisted YouTube or Loom; put link at top of README.
- **Do this LAST** — after Yash's Twilio and trace viewer work is merged.

**A4. Final submission packaging — 15 min**
- Verify checklist in playbook Section 15 is all green.
- Submit before 4:45 PM.

---

## Yash

**Y1. RELIABILITY_BRIEF "Failures found during build" section — 30 min**
- Playbook Section 13 point 6 says this section is worth points.
- List 3–5 real bugs we hit during the build with: symptom → root cause → fix (one paragraph each). Examples to draw from: any test that failed before it passed, any retry-loop edge case, any offer-expiry handling.
- If we don't have 5 real ones, write the 3 we have honestly rather than padding.

**Y2. Twilio live smoke test — 45 min**
- Twilio trial → number → verify both team phones.
- `ngrok http 8000`, paste URL into Twilio number's Messaging webhook (POST to `/sms`).
- Run `make server`, fire the "over threshold" scenario, get the SMS on your phone, reply "1", confirm the agent resumes and books.
- Screenshot phone + terminal trace → `docs/twilio_live.png`.
- Commit both.

**Y3. Trace viewer real run — 30 min**
- Delete the current `traces/sample_run.jsonl`.
- Run one clean end-to-end scenario (the one from A3 script — S02 approval flow) with fakes.
- Copy the resulting JSONL to `traces/sample_run.jsonl` so the viewer opens to a real trace.
- Open `viewer/index.html` in Chrome, verify every step renders with input/output expandable.

**Y4. Architecture diagram image — 20 min**
- Turn the ASCII diagram from the README into a real PNG using tldraw / excalidraw / whatever's fast.
- Save as `docs/architecture.png`, embed in RELIABILITY_BRIEF top.

---

## Sync checkpoints

| Time | Both check |
|------|-----------|
| **T+45 min** | Both live smoke tests (A1 Duffel + Y2 Twilio) working. Merge to main. |
| **T+90 min** | Y1 brief section drafted, Y3 real trace committed, A2 README audit done. |
| **T+2 hr** | A3 video recording. Y stops feature work — reviews video, fixes any typo. |
| **T+2:30 hr** | Freeze. Submit. |

## Order of merge (avoid conflicts)

- Y1 (RELIABILITY_BRIEF text) and A2 (README text) touch different files — safe in parallel.
- Y4 (architecture.png) and A1 (duffel_live.png) both add to `docs/` — safe.
- Y3 (trace file) is isolated.
- A3 (video link in README) is last; wait for A2 to land.
