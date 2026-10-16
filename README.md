# Daytona HackSprint #6 — SCRAM and REDLINE

Two entries, one repo.

| App | What it proves | Front end | Back end |
|---|---|---|---|
| **SCRAM** | An action-taking agent stayed within authority and recovered safely. | `scram/web` | `scram/api` (FastAPI) |
| **REDLINE** | A race-strategy call is supported by the data, compared with a baseline, and decided by a human. | `redline/web` | `redline/api` (FastAPI) |

**Co-owners, front end (UI/UX):** Natalie Walker, Nicole Rodriguez
**Co-owners, back end:** Jason Zeng, Aisling Leiva-Davila

Both front ends run on fixture data with no back end. Each `web/README.md` lists the endpoints and the exact JSON shape the back end returns.

## Run

```bash
cd scram/web && npm install && npm run dev      # SCRAM
cd redline/web && npm install && npm run dev    # REDLINE
```

## Deploy (Cloudflare Pages, free)

Create two Pages projects from this repo:

| Project | Root directory | Build command | Output |
|---|---|---|---|
| scram | `scram/web` | `npm run build` | `dist` |
| redline | `redline/web` | `npm run build` | `dist` |

Set `VITE_API_MODE=fixture` on both. The live demo with Daytona, Arize and the voice runs against the back end on a laptop.

## Sources of truth

- `docs/SCRAM_PRD.pdf`, `docs/SCRAM_BUILD_SPEC.md`
- `docs/REDLINE_PRD.pdf`, `docs/REDLINE_BUILD_SPEC.md`

Nothing here connects to a real reactor, vehicle or facility. Every outcome is simulated.
