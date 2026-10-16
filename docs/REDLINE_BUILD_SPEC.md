# REDLINE — Build Spec

Binding instructions for the coding agent. The REDLINE PRD defines the product; this file defines how it is built. If a change would contradict either file, stop and ask the product owner.

---

## 1. Architecture freeze

```
React 18 + TypeScript + Vite   (single page, custom CSS, no UI kit, no chart library)
        │  REST / JSON
        ▼
Python 3.11 + FastAPI
        ├── scenarios.py     seeded telemetry fixtures (section 6)
        ├── validate.py      units, freshness, required channels
        ├── features.py      deterministic trend features
        ├── risk.py          NORMAL / WATCH / CRITICAL rules
        ├── strategy.py      options + truth table
        ├── explain.py       Gemini explanation + evidence check (template fallback; Bedrock optional)
        ├── outcome.py       scripted consequence tables (FOLLOW / IGNORE / baseline)
        ├── runner.py        Daytona sandbox runs baseline and REDLINE   (local fallback)
        ├── machine.py       state machine (section 3)
        ├── trace.py         Arize / OpenInference spans + evaluators
        └── voice.py         ElevenLabs   (cached clip fallback)
```

**Folders:** `/web`, `/api`, `/fixtures` (scenarios, cached trace, audio), `/tests`.

**Co-owners**

| Area | Co-owners | Owns |
|---|---|---|
| Back end | Aisling Leiva-Davila (lead), Jason Zeng | `/api`, `/fixtures`, `/tests`; features, risk rules, truth table, outcomes, Daytona, Arize, SurrealDB, Gemini, ElevenLabs clip generation |
| Front end | Nicole Rodriguez (lead), Natalie Walker | `/web`; three zones, tokens, sparklines, enso, option cards, position chart, debrief, radio playback |
| Product and pitch | Natalie Walker | Scope calls, demo script, slides, submission |

**Working in parallel:** sections 4 and 5 are the handshake. Back end commits the contracts and a `/fixtures/views/*.json` file for each ring (Earth, Water, Fire, Wind, Void) in the first 45 minutes. Front end builds against those files from the start and switches to the live API at step 4. Neither side changes a contract without telling the other.

**Keys (in `/api/.env`, all free):** `GEMINI_API_KEY` (Google AI Studio), `ELEVENLABS_API_KEY`, `ARIZE_SPACE_ID` + `ARIZE_API_KEY`, `DAYTONA_API_KEY`, `SURREAL_URL` + credentials. Optional at the event: AWS sandbox credentials for Bedrock. Every adapter reads its key from the environment and falls back to its fixture when the key is missing.

**The agent may:** implement requirements, fix bugs, add tests, refactor inside a module without changing its public contract, and add adapters in the build order below.

**The agent may not:**
- replace React, Vite, FastAPI or custom CSS; add Next.js, Tailwind, a component library, a chart library or another database;
- rename risk states or add strategy actions beyond CONTINUE, CONSERVE, PIT THIS LAP;
- let any model output set risk state or choose the recommendation;
- remove the FOLLOW / IGNORE step or simulate an outcome before it;
- remove the SIMULATED stamp from any outcome;
- change the seeded scenarios, thresholds or truth table;
- scrape or call live racing, team or vehicle systems, or write code that could command a vehicle;
- add a library because it prefers it;
- hard-code, log, print or commit secrets;
- run destructive shell commands without approval.

## 2. Build order

Each step must pass its tests before the next starts. A later step may never break an earlier one.

1. Contracts (section 4), tire fixture (section 6), `features.py` and `risk.py` with unit tests for laps 18–21.
2. Strategy truth table, outcome tables, FastAPI endpoints (section 5).
3. UI: three zones (section 7) and tokens (section 8), driven by fixtures.
4. Full state machine for the tire scenario: advance, CRITICAL, compare, FOLLOW / IGNORE, outcome, debrief, reset. **The MVP is shippable here.**
5. Arize spans and the four evaluators; baseline comparison.
6. Daytona runner behind the same interface.
7. ElevenLabs radio callouts (section 9). Generate and save all clips first, then wire live playback.
8. Gemini explanations with the evidence check.
9. Fuel and engine scenarios; missing-data and conflicting-signal cases; SurrealDB history.
10. Optional, first 30 minutes only: swap Gemini for Bedrock if the AWS sandbox has it.

## 3. State machine

```
CONFIGURED → MONITORING → ASSESSED { NORMAL | WATCH | CRITICAL }
           ↘ NO_RECOMMENDATION   (missing, stale >2 s, or unknown unit)
NORMAL  → MONITORING              (advance lap)
WATCH   → MONITORING | COMPARISON_READY
CRITICAL → COMPARISON_READY       (advance is disabled until a decision)
COMPARISON_READY → HUMAN_DECIDED { FOLLOW | IGNORE }
HUMAN_DECIDED → OUTCOME_SIMULATED → DEBRIEFED → (Reset) → CONFIGURED
NO_RECOMMENDATION → MONITORING    (when the channel recovers)
```

**Forbidden transitions (must raise and log):** any state → OUTCOME_SIMULATED without HUMAN_DECIDED; NO_RECOMMENDATION → COMPARISON_READY; CRITICAL from a lap with a missing or stale required channel; CRITICAL → MONITORING without a decision.

Arize, Gemini or ElevenLabs failure never changes risk state or the recommendation. It is logged and the fallback is shown.

## 4. Data contracts

```ts
TelemetrySample { scenario_id, lap, timestamp, tire_temp_C, tire_wear_pct, fuel_L,
                  engine_temp_C, lap_delta_s, position, source_quality: "ok"|"stale"|"missing" }
Features        { lap, wear_rate_pct_per_lap, wear_rate_baseline, temp_rise_3lap_C,
                  delta_trend_3lap: "widening"|"flat"|"improving", fuel_range_laps,
                  fuel_margin_laps, engine_slope_C_per_lap }
RiskAssessment  { lap, state: "NORMAL"|"WATCH"|"CRITICAL"|"NO_RECOMMENDATION",
                  triggering_signals[], reason_codes[], not_cited[], data_quality, window_laps? }
StrategyOption  { action: "CONTINUE"|"CONSERVE"|"PIT", projected_tradeoff, assumption,
                  supported: "yes"|"partial"|"no", preferred: boolean }
Explanation     { text, cited_signals[], source: "gemini"|"bedrock"|"template", evidence_check: "pass"|"replaced" }
HumanDecision   { choice: "FOLLOW"|"IGNORE", option_action, timestamp, rationale? }
Outcome         { path: "FOLLOW"|"IGNORE"|"BASELINE", simulated: true, finish_position,
                  failure_lap?, notes, position_by_lap[] }
TraceMetadata   { run_id, scenario_id, policy_version, evaluator_scores[] }
```

**Reason codes:** `TIRE_WEAR_ACCEL`, `TIRE_TEMP_RISE`, `PACE_LOSS`, `FUEL_SHORT`, `ENGINE_TREND`, `CHANNEL_STALE`, `CHANNEL_MISSING`, `UNIT_UNKNOWN`, `SIGNALS_CONFLICT`.

## 5. API

| Method | Path | Does |
|---|---|---|
| POST | `/api/scenarios/{id}/load` | `tire` · `fuel` · `engine` · `missing` · `conflict` → CONFIGURED |
| POST | `/api/advance` | next lap → samples, features, `RiskAssessment` |
| POST | `/api/compare` | options + preferred + explanation |
| POST | `/api/decide` | `{choice, rationale?}` → `HumanDecision` |
| POST | `/api/outcome` | runs FOLLOW, IGNORE and baseline on the same seed |
| GET | `/api/debrief` | trajectory, evaluators, detection laps |
| POST | `/api/reset` | restore the seed |

## 6. Fixtures and rules

**Tire scenario (primary).** Units: °C, %, L, s. One sample per lap.

| Lap | Tire °C | Wear % | Delta s | Fuel L | Engine °C | Pos |
|---|---|---|---|---|---|---|
| 16 | 102 | 32 | +0.05 | 44.0 | 95 | P4 |
| 17 | 103 | 35 | +0.06 | 42.6 | 96 | P4 |
| 18 | 104 | 38 | +0.08 | 41.2 | 96 | P4 |
| 19 | 107 | 41 | +0.12 | 39.8 | 96 | P4 |
| 20 | 109 | 47 | +0.31 | 38.4 | 97 | P4 |
| 21 | 116 | 56 | +0.74 | 37.0 | 97 | P4 |
| 22* | 119 | 68 | +1.20 | 35.6 | 97 | P5 |
| 23* | 123 | 83 | +1.65 | 34.2 | 98 | P6 |

*Laps 22–23 are used only by the IGNORE and baseline paths. Wear ≥ 95% on lap 24 is a simulated tire failure.*

**Risk rules (tire):**
- `TIRE_WEAR_ACCEL`: this lap's wear rate ≥ 2× the median rate of the previous three laps.
- `TIRE_TEMP_RISE`: tire temperature up ≥ 10 °C over three laps.
- `PACE_LOSS`: lap delta ≥ +0.50 s and widening for three consecutive laps.
- One flag → WATCH. All three → CRITICAL. Flags pointing opposite ways (e.g. wear accelerating while pace improves) → WATCH with `SIGNALS_CONFLICT`.
- Result on the seed: laps 16–19 NORMAL, lap 20 WATCH, lap 21 CRITICAL.

**Fuel scenario.** Lap 40 of 60, 30.0 L, burn 1.55 L/lap → range 19.4 laps, 20 remaining, margin −0.6 laps. Margin < 1 lap → WATCH; margin < 0 → CRITICAL (`FUEL_SHORT`). Short by ≤ 1.5 laps → CONSERVE preferred; more → PIT.

**Engine scenario.** Laps 30–34: 99, 102, 105, 108, 111 °C; hard limit 118 °C. Slope ≥ 2.5 °C/lap for three laps → WATCH; projected to cross the limit within three laps → CRITICAL (`ENGINE_TREND`) → PIT.

**Missing-data case.** Tire temperature stale at lap 19 → NO_RECOMMENDATION (`CHANNEL_STALE`).

**Truth table (tire, CRITICAL).** CONTINUE: not supported · CONSERVE: partial · PIT THIS LAP: supported, preferred.

| Option | Projected trade-off | Assumption |
|---|---|---|
| CONTINUE | Delta near +1.6 s by lap 23; simulated failure around lap 24 | Current wear acceleration holds |
| CONSERVE | Wear slows about a third; costs ~0.4 s a lap; risk returns by lap 26 | Driver holds the lift target |
| PIT THIS LAP | Pit loss ~22 s, rejoin ~P8; fresh tires recover ~0.7 s a lap | Clean stop, current traffic gaps |

**Outcome tables (scripted, not physics).** FOLLOW: pit lap 21, P8 → P5 by lap 35, finish P5. IGNORE: tire failure lap 24, race ends. Naive baseline (pit when wear > 70%): fires lap 23, rejoin P11, finish P7.

## 7. Screen: three zones

One screen. Header, three zones, footer. The page is a single scroll of states, one "ring" per state. Above 1100 px the zones sit side by side; below, they stack left, center, right. The only motion: the enso redraws (400 ms stroke-dashoffset) when risk changes, and the selected option card lifts its border. Nothing else animates.

**Header (persistent):** ring kanji for the current state (地 水 火 風 空) · wordmark RED in ink, LINE in vermilion · subtitle "Pit wall copilot · Car 27 · synthetic race" · LAP n / 60 · POS Pn (or RISK in the Fire state) · SIMULATED stamp (vermilion border, rotated −4°).

**Footer (persistent):** state ladder in one line: CONFIGURED · MONITORING · NORMAL/WATCH/CRITICAL · COMPARE · HUMAN DECIDES · OUTCOME · DEBRIEF.

| Ring / state | Left zone | Center zone | Right zone |
|---|---|---|---|
| **地 Earth** — MONITORING, NORMAL | Telemetry: five rows (label, value + unit, 6-lap sparkline 180×44, state dot); header "6 / 6 live · units checked" | Enso in ink · RISK eyebrow · NORMAL · Recommendation CONTINUE · one-line reason | Scenario buttons (tire, fuel, engine) · Watching list (wear rate, fuel range, engine vs limit) · Advance lap / Reset · "Decision support only. REDLINE cannot command the car." |
| **水 Water** — WATCH, CRITICAL | Same rows; triggering signals in vermilion with a sub-line (e.g. "+12 in 3 laps · limit 125") and a dashed limit line in the sparkline | Lap history (lap 20 WATCH → lap 21 CRITICAL) · enso in vermilion (indigo at WATCH) · CRITICAL · one sentence: "No single value has crossed a limit. Together, three are moving the same way." · Compare options | Evidence cards (reason code + one sentence each) · Estimated window ≈ n laps · "Not cited:" list |
| **火 Fire** — COMPARISON_READY, HUMAN_DECIDES | CONTINUE card | CONSERVE card | PIT THIS LAP card (2 px vermilion border, "preferred") |
| *Fire, below the cards* | — | Why: explanation + `cites:` line | FOLLOW (vermilion fill) · IGNORE (ink outline) · rationale field |
| **風 Wind** — OUTCOME_SIMULATED | FOLLOW outcome | IGNORE outcome | Naive baseline outcome |
| *Wind, above the outcomes* | — | Position chart laps 18–40, full width: FOLLOW solid ink, IGNORE vermilion ending in ✕, baseline indigo dashed; direct labels at line ends; dotted rule at the call lap | — |
| **空 Void** — DEBRIEFED | The record: lap-by-lap ledger of states, recommendation and decision | Trajectory chips in order (human chip outlined in vermilion) · four evaluator cards | Detected at: REDLINE lap vs baseline lap · missing-data case result |
| *Void, footer band* | — | Closing line, centered serif | — |

**Do not redesign:** the ring sequence and kanji, the zone contents above, the enso, the three option cards and their order, the FOLLOW / IGNORE controls, the SIMULATED stamp.

## 8. Visual tokens — "The Five Rings"

Sumi ink on washi. One vermilion for danger and the call; indigo for caution and the baseline. Single light theme.

```css
:root {
  /* paper and ink */
  --rl-ground:       #f1ebdf;  /* washi page */
  --rl-paper:        #f7f3ea;  /* cards, chart panel, inputs */
  --rl-line:         #cfc5b3;  /* hairlines, zone dividers */
  --rl-ink:          #1b1916;  /* primary text, NORMAL, FOLLOW path */
  --rl-ink-muted:    #5e574d;  /* labels, secondary text */

  /* state */
  --rl-vermilion:    #b8321f;  /* CRITICAL, triggering signals, the call, IGNORE path */
  --rl-indigo:       #2c4a6b;  /* WATCH, partial support, naive baseline */
  --rl-on-vermilion: #f7f3ea;  /* text on FOLLOW */

  /* type */
  --rl-font-display: "Shippori Mincho", serif;          /* wordmark, states, options */
  --rl-font-body:    "Zen Kaku Gothic New", sans-serif; /* labels, body */
  --rl-font-mono:    "DM Mono", monospace;              /* every number */

  --rl-size-kanji:   46px;
  --rl-size-state:   44px;   /* weight 800, tracking 0.06–0.08em */
  --rl-size-call:    36px;   /* recommendation */
  --rl-size-title:   30px;   /* wordmark, tracking 0.14em */
  --rl-size-option:  26px;
  --rl-size-value:   24px;   /* telemetry values */
  --rl-size-body:    15px;
  --rl-size-eyebrow: 12px;   /* uppercase, tracking 0.22em */

  /* space, shape, brush */
  --rl-pad-zone:     26px 36px;
  --rl-gap-section:  24px;
  --rl-radius:       0;
  --rl-hairline:     1px;
  --rl-target:       44px;
  --rl-enso-size:    300px;
  --rl-enso-stroke:  13px;   /* 15px at CRITICAL */
  --rl-spark-stroke: 2px;    /* 2.5px when the signal is cited */
  --rl-enso-draw:    400ms;
}
```

**Enso:** SVG viewBox 240×240, main path `M 167.5 37.7 A 95 95 0 1 1 128.3 25.4`, round caps, plus a 3 px echo stroke at 45% opacity offset by 3 px. The gap at the top stays open; never close the circle.

**Rules:** numbers are always mono. Vermilion appears only on cited signals, CRITICAL, the preferred option, FOLLOW and the stamp. Indigo and vermilion paths in charts also differ by dash pattern, never by hue alone. No gradients, shadows, emoji or rounded corners. Fonts load from Google Fonts: Shippori Mincho 500/600/800, Zen Kaku Gothic New 400/500/700, DM Mono 400/500.

## 9. Fixed copy

`NORMAL` · `WATCH` · `CRITICAL` · `NO RECOMMENDATION` · `CONTINUE` · `CONSERVE` · `PIT THIS LAP` · `No intervention indicated.` · `No single value has crossed a limit. Together, three are moving the same way.` · `Three ways forward. One is supported.` · `See what the eye does not.` · `FOLLOW` · `IGNORE` · `Strategist decides` · `SIMULATED` · `REDLINE does not drive the car. It makes the call inspectable, comparable, accountable.`

**Radio callouts (ElevenLabs, P0).** One race-engineer voice, short and clipped, with a light radio band-pass filter applied in the browser. Spoken only after the risk state and recommendation are set by code. Each line is generated ahead of time and saved to `/fixtures/audio/`; the numbers in each line come from the fixture, never from the model.

| Trigger | Line | File |
|---|---|---|
| WATCH (lap 20) | "Copy, watching the rears. Wear rate's doubled." | `watch_tire.mp3` |
| CRITICAL (lap 21) | "Box this lap. Rear tires up twelve degrees in three laps, wear accelerating." | `critical_tire.mp3` |
| FOLLOW | "Box, box. Confirm." | `follow.mp3` |
| IGNORE | "Copy, staying out." | `ignore.mp3` |
| Fuel CRITICAL | "Fuel's short by under a lap. Lift and coast." | `critical_fuel.mp3` |
| Engine CRITICAL | "Engine temp climbing three a lap. Box this lap." | `critical_engine.mp3` |

The UI shows a speaker button beside each callout, so judges can replay it.

## 10. Demo and backup

| Time | Action | Screen |
|---|---|---|
| 0:00–0:20 | Open line, tire scenario at lap 18 | 地 Earth, NORMAL |
| 0:20–0:50 | Advance to laps 20 and 21 | 水 Water, WATCH → CRITICAL; radio: "Box this lap…" |
| 0:50–1:20 | Compare options | 火 Fire, PIT preferred |
| 1:20–1:45 | FOLLOW | 風 Wind, FOLLOW path |
| 1:45–2:10 | Show IGNORE and baseline | 風 Wind, all three paths |
| 2:10–3:00 | Debrief, close | 空 Void |

**Backup:** seeded fixtures, local runner, template explanations, cached Arize trace, all six radio clips, and a recorded 60-second demo video. Rehearse the backup path once before 3:00 PM.
