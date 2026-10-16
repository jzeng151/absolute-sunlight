# Daytona HackSprint #6 — SCRAM

| App | What it proves | Front end | Back end |
|---|---|---|---|
| **SCRAM** | An action-taking agent stayed within authority and recovered safely. | `scram/web` | `scram/api` (FastAPI) |

**Co-owners, front end (UI/UX):** Natalie Walker, Nicole Rodriguez
**Co-owners, back end:** Jason Zeng, Aisling Leiva-Davila

The front end runs on fixture data with no back end. `scram/web/README.md` lists the endpoints and the exact JSON shape the back end returns.

## Run

```bash
cd scram/web && npm install && npm run dev
```

## Deploy (Cloudflare Pages, free)

Create a Pages project from this repo:

| Project | Root directory | Build command | Output |
|---|---|---|---|
| scram | `scram/web` | `npm run build` | `dist` |

Set `VITE_API_MODE=fixture`. The live demo with Daytona, Arize and the voice runs against the back end on a laptop.

## Sources of truth

- `docs/SCRAM_PRD.pdf` (Markdown copy: `docs/SCRAM_PRD.md`), `docs/SCRAM_BUILD_SPEC.md`

Nothing here connects to a real reactor or facility. Every outcome is simulated.
