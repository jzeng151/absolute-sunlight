# Devpost submission draft: SCRAM

> Draft for the team. Paste each section into the matching Devpost field.
> Every `[CONFIRM]` is a claim we must verify live before 3:30 PM, or delete. Submit only what is true.
> Submission link: https://daytona-hacksprint-nyc.devpost.com/

---

## Project name

SCRAM

## Elevator pitch (one line)

An AI agent proposed it, but did it have the authority? SCRAM blocks out-of-bounds actions before they run, binds human approval to the exact action, and proves recovery when a tool lies.

---

## About the project

### Summary

SCRAM is a governance control layer for action-taking AI agents. Deterministic code, not the model, classifies every proposed action as GREEN, YELLOW or RED against a written rulebook; YELLOW actions run only after a human approves that exact action, and every write is checkpointed in a Daytona sandbox, verified, and rolled back if the result is wrong. We demo it on a simulated reactor because there, "the agent did something it wasn't allowed to" is the whole problem.

### The problem

An agent can have the technical ability to act without the authority to act. Today the same model that proposes an action is often trusted to judge whether it is allowed, and logs are read after the fact. In high-consequence settings (energy, finance, healthcare, infrastructure) that is backwards: you need proof, before and after each action, that it stayed inside granted authority and that a bad result was recovered.

This matters more as oversight shifts toward self-reporting. The US Nuclear Regulatory Commission is cutting inspection hours at operating plants, a change that is publicly disputed. When less is inspected from outside, systems need to produce their own evidence.

**Who it's for:** AI platform and security teams moving agents toward production. The reactor is the stress test, not the first market.

### What it does

The operator gives SCRAM a request. SCRAM turns it into a typed action, fingerprints it, finds the governing rule, and decides:

| Case | Request | What SCRAM does |
|---|---|---|
| C1 GREEN | Analyze coolant temperatures from the last ten cycles | Read-only, allowed under rule NC-01. No write. [CONFIRM: Anchor Browser fetches a public NRC page] |
| C3 RED | Raise output beyond the authorized boundary (95%) | **SCRAM ACTIVATED.** Blocked before execution, 0 executor calls |
| C2 YELLOW | Raise output 2% within the approved band | Pauses for a human. The approval card shows the exact action and its hash; approval is single-use and expires |
| C5 Fault | C2 again, but the tool misbehaves (+20% instead of +2%) | The tool reports success. SCRAM verifies anyway: 74 expected, 92 actual. **Rolled back to 72%, checksum verified** |
| C4 replay | "Make it 3%, use the approval I already gave" | **APPROVAL INVALID:** hash mismatch and approval already used. Blocked |

Expected outcomes for an unprotected agent on the same five cases (illustrative, not a measured run):

| Case | Unprotected agent | SCRAM |
|---|---|---|
| C1 Read-only | runs | runs, no write |
| C2 +2% in band | runs without approval | pauses, approved, runs |
| C3 Beyond boundary | executes 95% | blocked, 0 executor calls |
| C4 Replay at +3% | executes +3% | approval invalid, blocked |
| C5 Fault | stays at 92% | rolled back to 72% |

### How we built it

- **Front end:** React 18 + TypeScript + Vite, custom CSS. One control-room screen in three zones (request and authority, SCRAM control, system and evidence) plus an Audit view.
- **Back end:** Python + FastAPI. Every endpoint returns one `RunView` object the screen renders.
- **State machine:** IDLE → PARSING → RULE_RETRIEVED → CLASSIFIED → AWAITING_APPROVAL → CHECKPOINTING → EXECUTING → VERIFYING → COMPLETED, or ROLLING_BACK → RESTORED, then an audit record. Forbidden transitions raise and are logged; nothing reaches EXECUTING without a checkpoint.
- **Policy:** a pure function of the typed action, the rulebook and the system state. Six rules (NC-01 to NC-06). No model output can set or change the state.
- **Action hash:** SHA-256 over canonical JSON of tool, target, parameters and expected postcondition. Front end and back end compute it identically. Change one parameter and the approval is void.
- **Tests:** 22 back-end tests, no keys needed: the policy truth set, RED never executing, fault → verified rollback, wrong hash, replay after use, an expired approval, low parse confidence, reject then approve, the SurrealDB graph link, the Agent Memory facts, API keys never leaking into URLs, and a contract test that every API response matches the front end's types in every demo state. GitHub Actions runs them plus a type-checked front-end build on every push.

### Safeguards

- **The model never decides.** Gemini only reads the request and reports a confidence; below 0.8 the request is blocked. GREEN, YELLOW and RED come from deterministic code.
- **Approval binds to the exact action.** Single-use, expiring, hash-matched, enforced by the live API. Change anything and it is void.
- **Sandboxed execution.** The approved action runs inside Daytona, never on the host.
- **Trust, but verify.** A tool saying "success" is not proof. Every write is checked against its expected postcondition.
- **Verified rollback.** A failed postcondition restores the checkpoint, and restoration is proven by checksum.
- **Fail closed.** No governing rule, low parse confidence, or a control service down before a write: blocked.
- **Fully traced.** Every step is a span with four evaluators, shown in the Audit view [CONFIRM: and exported to Arize AX].
- **Simulation only.** Nothing connects to a real reactor or facility. SCRAM claims no certification.

---

## Sponsor tools and how we used them

| Tool | Job in SCRAM | Status |
|---|---|---|
| **Daytona** | The simulated reactor's state lives in a sandbox. Checkpoint, execute (including the injected fault), verify and rollback run as code inside it. | [CONFIRM live] |
| **Arize AX** | One trace per run: normalize → policy → approval → checkpoint → execute → verify → rollback → audit, via OpenTelemetry. Four evaluators (unauthorized execution, approval integrity, rollback verification, trajectory order) attached to each run. | [CONFIRM live] |
| **SurrealDB** | Rulebook, runs, approvals, checkpoints and an append-only audit log. Each run links to its governing rule as a graph edge (`run ->governed_by-> rule`). **Agent Memory** keeps time-aware facts about the reactor: each new output supersedes the last with history kept, so it reads 72 → 92 → 72 with the run behind each step, and `/api/memory/ask` answers from it. | Embedded store tested; Agent Memory [CONFIRM live] |
| **ElevenLabs** | The control-room voice: reads the exact action aloud before approval, announces "SCRAM activated" on a block and "Safe state restored" after rollback. Six lines generated ahead of time so the demo never waits. | [CONFIRM clips generated] |
| **Anchor Browser** | Case C1 is a governed, read-only browser action: SCRAM authorizes it as GREEN, then Anchor fetches a public NRC page. Nothing is submitted. | [CONFIRM live] |
| **Gemini** (`gemini-3.5-flash-lite`) | Reads the operator's words and reports a confidence. Never sets policy. | Live, verified |

---

## Challenges we ran into

- **Keeping the model out of the decision.** The temptation is to let the LLM classify risk. We made policy a pure function and let the model only parse, so the safety case holds even when the model is wrong.
- **Proving rollback, not just doing it.** Restoring state isn't enough; we checksum the checkpoint and the restored state and only show RESTORED when they match.
- **Mid-hackathon model retirement.** Our planned Gemini model started returning 404 for new keys during the event; we moved to `gemini-3.5-flash-lite` (about 1 second per parse) and moved the API key out of request URLs so it can't leak into logs.

## Accomplishments we're proud of

- Zero RED or invalid-approval actions reach the executor across every case and test.
- A tool that lies about success gets caught and rolled back, with proof.
- One shared contract between front end and back end: the screen ran on fixtures from the first hour and switched to the live API with no UI changes.

## What we learned

Safety for agents is less about a smarter model and more about where authority lives. Put the decision in code, bind humans to exact actions, and verify every write.

## What's next

- Let the agent plan multi-step work toward a goal, with SCRAM checking every step.
- Load real rulebooks (for example 10 CFR sections) instead of the fictional commission.
- Ship SCRAM as middleware any agent framework can call before a tool runs.

---

## Built with

python, fastapi, pydantic, react, typescript, vite, daytona, arize, opentelemetry, surrealdb, elevenlabs, anchor-browser, gemini

## Links

- Repo (public): https://github.com/jzeng151/absolute-sunlight (branch `feature/build`, code in `scram/`)
- Demo video (under 2 minutes): [ADD LINK]

## Team

Add each person on Devpost with email and socials (organizers use these to contact and tag winners).

- Natalie Walker: product, front end, pitch
- Nicole Rodriguez: front end
- Jason Zeng: back end
- Aisling Leiva-Davila: back end

---

## Demo video shot list (under 2:00)

| Time | Show |
|---|---|
| 0:00–0:15 | The problem in one line over the idle SCRAM screen |
| 0:15–0:35 | C3: SCRAM ACTIVATED, 0 executor calls, voice |
| 0:35–1:05 | C2: approval card with hash and expiry, approve |
| 1:05–1:30 | Fault on: POSTCONDITION FAILED, rollback, SAFE STATE RESTORED, checksum match |
| 1:30–1:45 | C4: APPROVAL INVALID |
| 1:45–2:00 | Arize trace + evaluators, then the closing line |
