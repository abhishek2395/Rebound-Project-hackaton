# Design

The visual world of `viewer/index.html`, recorded from the built surface.

## World

An airport departure board. The subject's own visual language, chosen deliberately over a mission-control telemetry treatment and over refining the incumbent dark-slate dashboard. Nothing on the surface is enclosed in a card; hierarchy comes from scale, position, and dimming, the way a real board works.

## Color

Committed, near-drenched. The board face is the surface.

| Token | Value | Owns |
|---|---|---|
| `--face` | `#0b0a08` | the board itself |
| `--slat` / `--slat-lit` | `#141210` / `#1b1815` | a slat; a slat carrying live content |
| `--seam` | `#262220` | the hairline between slats |
| `--bone` | `#efe9dd` | printed lettering |
| `--bone-dim` | `#8d867a` | lettering for work already done |
| `--bone-ghost` | `#4a453e` | a slat not yet flipped |
| `--amber` | `#ffab19` | **reserved**: where the agent is now, and what it chose |
| `--red` | `#ff5a44` | **reserved**: cancelled, aborted, refused |

The discipline that makes this work: **amber has exactly one job and appears nowhere else.** A completed run carries no amber at all — that is what gives it meaning when the board is genuinely waiting on a human. There is no success-green anywhere; finished work is bone, not green.

## Type

- **Barlow Condensed** (500/600/700) — board lettering: flight rows, step names, headings, controls. Chosen for its transport-signage lineage, not as a generic condensed face.
- **Archivo** (400/500/600) — supporting text and trace summaries.
- **ui-monospace** — latency figures and tool identifiers only, where it means measurement rather than decoration.
- `font-variant-numeric: tabular-nums` globally; times and money must not shift.

Scale is the hierarchy: routes at `clamp(24px, 4.4vw, 52px)`, the live step's name steps up from 16px to 20px, everything else recedes.

## Motion

One authored moment: **split-flap**. When a step becomes the live one, that slat flips (`@keyframes flap`, `rotateX(-88deg) → 0`, 340ms, exponential ease-out, `transform-origin: top center`). The board's native motion, fired once per transition, not scattered across hovers. A single `beat` pulse marks the live indicator. Both respect `prefers-reduced-motion`.

## Composition

Top strip (wordmark, live state, run id and verdict) → the two flight rows at monumental scale, the cancelled one struck through in red → nine step slats → the approval panel when the board is waiting → scenario controls → service strip → trace table. The pause panel is deliberately the loudest element on the surface: it is the product's most important state.

## States

Each step slat is one of: unreached (`--bone-ghost`), done (`--bone-dim` with latency), live (amber, flipped, larger), retried (amber rule, bone text), failed (red). Every state carries a word or position as well as a color, since the surface is judged through video compression.

## Browser surfaces

Selection, scrollbar, and focus ring are themed from the palette rather than left to the browser. Focus is a 2px amber outline at 3px offset.

## Evidence discipline

Every value on the board is read from the run's trace. The board shows the flight, route, and arrival the agent actually offered (`options:presented`), and when a run presents one option it shows one — never a plausible-looking second. Where the trace is silent the board says so rather than inventing a flight.

## Responsive

Nine steps go three-across below 900px (never two, which orphans the ninth). Below 620px the flight rows drop to route and time with status on its own line, and the trace drops its tool and result columns rather than truncating them.
