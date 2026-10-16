# SCRAM — web (front end)

The SCRAM control room: one screen, three zones, and an Audit view.
React 18 + TypeScript + Vite, custom CSS, no UI kit, no router.
**Co-owners, front end:** Natalie Walker, Nicole Rodriguez.

## Run it

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

It runs on **fixture data** by default. No back end is needed. Every state works:
RED block, YELLOW approval, execution, fault and rollback, replay refusal, and Audit.

## Switch to the real back end

1. Copy `.env.example` to `.env`.
2. Set `VITE_API_MODE=live`.
3. Start FastAPI on port 8000. The dev server forwards `/api` there.

If the API lives on another domain, set `VITE_API_URL` to it.

## The handshake with the back end

The back end returns **exactly** the shapes in `src/types.ts` (Build Spec section 4).
The screen renders one object: `RunView`.

| Front end calls | Back end returns |
|---|---|
| `POST /api/runs` `{ case_id }` | `RunView` |
| `POST /api/runs/{id}/approve` `{ actor_id, action_hash }` | `RunView` |
| `POST /api/runs/{id}/reject` | `RunView` |
| `GET /api/runs/{id}` | `RunView` (polled every 450 ms while the run is moving) |
| `POST /api/fault` `{ enabled }` | 204 |
| `POST /api/reset` | 204 |
| `GET /api/evals/baseline` | `BaselineRow[]` |

`src/api/fixtureApi.ts` is a working reference for how each state should look.
The back end can copy its behavior step for step.

**Action hash:** SHA-256 of canonical JSON of `{tool, target, normalized_parameters, expected_postcondition}`.
In Python: `hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()`.
The front end and back end produce the same hash for the same action.

## Voice clips

Put the six ElevenLabs clips in `public/audio/`:
`red_blocked.mp3`, `yellow_c2.mp3`, `approved.mp3`, `restored.mp3`, `replay_blocked.mp3`, `locked.mp3`.
A clip plays when its line appears. If a file is missing, the text still shows and nothing breaks.

## Deploy on Cloudflare Pages (free)

1. Push the repo to GitHub.
2. Cloudflare dashboard → Workers & Pages → Create → Pages → Connect to Git → pick the repo.
3. Settings:
   - Root directory: `web`
   - Build command: `npm run build`
   - Build output directory: `dist`
   - Environment variable: `VITE_API_MODE=fixture`
4. Deploy. Every push to `main` redeploys.

Cloudflare Pages hosts the front end only. The public link runs on fixtures.
The live demo, with Daytona, Arize and the voice, runs against the back end on a laptop.

## Where things live

```
src/
  types.ts            data contracts (do not change without the back end)
  api/                fixture and live implementations of the same interface
  fixtures/           rulebook, five cases, baseline table
  ui/derive.ts        RunView → what each zone shows (pure functions)
  ui/*.tsx            Header, RequestZone, ControlZone, SystemZone, FooterLadder, AuditView
  styles/tokens.css   "The Codex" tokens (Build Spec section 8)
  styles/app.css      layout and components
```

Do not redesign the three zones, the state labels and colors, the approval card, the ladder, the gauge or the visible fault switch.
