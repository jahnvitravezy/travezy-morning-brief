# Travezy Morning Brief

Travezy's first multi-agent feature (Brief 6). Each morning of a trip, the traveller gets one
notification with their day, anything they need to know, and two or three things they could add
to it. Four agents build the brief, an AI judge checks it, and an evaluation framework compares
models for production.

**Code computes the facts; the models write the words.**

- Setup and commands: [`docs/setup.md`](docs/setup.md)
- Design note (decisions, checks, notification controls, privacy, cost): [`docs/design_note.md`](docs/design_note.md)
- Architecture diagram: [`docs/architecture.html`](docs/architecture.html)
- Build log with every run and result: [`docs/build_log.md`](docs/build_log.md)

## Quick start

```powershell
Copy-Item .env.example .env      # then add your keys
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/health
Start-Process http://localhost:8000/docs
```

## What's in it

- **Context layer:** one package of facts per trip day (itinerary, preferences, time zone,
  weather, Google Places, travel times, data-quality flags), built once, identified by a `ctx_id`.
- **Agents:** Summariser and Surprise finder in parallel, then the Composer. Timeline, spend,
  clashes and the send time are computed in code.
- **Judge:** rule checks in code, then scores per criterion and a list of serious errors; code
  decides send, fix once, or safe fallback. A serious error fails the brief whatever its score.
- **Evaluation:** fixed test set of 22 days, one role changed at a time, scorecard, judge
  consistency and self-preference tests, cost report, blind review pack and agreement scoring.
- **Service:** FastAPI + Postgres outbox, batch runner, web preview, kill switch, budget cap.

## Layout

| Folder | What's in it |
|---|---|
| `config/` | Agent registry, sources, models, rubrics, prices, app settings, test sets |
| `mb/` | Contracts, context, sources, agents, judge, pipeline, API, batch, evals |
| `prompts/` | Versioned prompts, one file per version |
| `data/` | Sample itineraries, trips, preferences, weather scenarios |
| `tests/` | Automated tests, no network or cost |
| `docs/` | Setup, design note, architecture, build log |

Model and API usage is capped by a budget guard; every paid call is logged.
