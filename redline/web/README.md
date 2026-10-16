# REDLINE — web (front end)

The pit wall copilot: five rings, three zones per ring.
React 18 + TypeScript + Vite, custom CSS, no UI kit, no chart library, no router.
**Co-owners, front end:** Natalie Walker, Nicole Rodriguez.

## Run it

```bash
cd redline/web
npm install
npm run dev        # http://localhost:5173
```

It runs on **fixture data** by default. All three scenarios work: tire degradation (primary), fuel window and engine heat.

| Ring | State | Screen |
|---|---|---|
| 地 Earth | MONITORING · NORMAL | Telemetry, enso, CONTINUE |
| 水 Water | WATCH / CRITICAL | Cited signals in vermilion, evidence, radio call |
| 火 Fire | COMPARISON_READY | Three options, why, FOLLOW / IGNORE |
| 風 Wind | OUTCOME_SIMULATED | Position chart: follow vs ignore vs naive baseline |
| 空 Void | DEBRIEFED | Ledger, trajectory, evaluators, detection lap |

Header controls: text size (A− / A+) and light / dark (ink on washi / ink-wash night).

## Switch to the real back end

Set `VITE_API_MODE=live` in `.env` and start FastAPI on port 8000.

## The handshake with the back end

Every endpoint returns one object, `RaceView` (`src/types.ts`).

| Front end calls | Back end returns |
|---|---|
| `POST /api/scenarios/{id}/load` | `RaceView` |
| `POST /api/advance` | `RaceView` |
| `POST /api/compare` | `RaceView` |
| `POST /api/decide` `{ choice, rationale }` then `POST /api/outcome` | `RaceView` |
| `GET /api/debrief` | `RaceView` |
| `POST /api/reset` | `RaceView` |

`src/api/risk.ts` holds the deterministic risk rules from Build Spec section 6, as pure functions.
The back end can port them line for line. `src/fixtures/scenarios.json` holds the seeded laps, options and outcome tables.

## Radio clips

Put ElevenLabs clips in `public/audio/`: `watch_tire.mp3`, `critical_tire.mp3`, `watch_fuel.mp3`, `critical_fuel.mp3`, `watch_engine.mp3`, `critical_engine.mp3`, `follow.mp3`, `ignore.mp3`.
Missing clips are skipped; the text still shows.

## Deploy on Cloudflare Pages

Same as SCRAM, with root directory `redline/web`, build command `npm run build`, output `dist`.
