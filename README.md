# Travezy Morning Brief

Every morning of a trip, the traveller gets one short message about their day: what's planned,
anything they should know before they head out, and a couple of things they could add if they
have time. Tap it, and the full brief opens.

This repository is the service that writes that brief. It's Travezy's first multi-agent feature
(Brief 6): three agents write the brief, a fourth checks it, and an evaluation framework measures
which models to use and whether a change made things better or worse.

The idea everything rests on is simple: **code works out the facts, and the models only write
the words.** Times, travel, opening hours, weather and costs come from real sources and are
calculated in code, so a brief can't make them up.

## Where to start

- **Run it:** [`docs/setup.md`](docs/setup.md), one command with Docker
- **Understand it:** [`docs/design_note.md`](docs/design_note.md), the decisions, the checks, cost and what's still open
- **Run it in production:** [`docs/production_notes.md`](docs/production_notes.md) (schedule, scaling, keys, retention), [`docs/loader_spec.md`](docs/loader_spec.md) (the fields the loader needs) and [`docs/cost_sheet.md`](docs/cost_sheet.md)
- **See it:** [`docs/architecture.html`](docs/architecture.html), how the parts fit together
- **Follow how it was built:** [`docs/build_log.md`](docs/build_log.md), every run, result, bug and fix, by date

## Quick start

```powershell
Copy-Item .env.example .env      # Windows: then add your keys, MB_API_KEY and POSTGRES_PASSWORD
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/health
```

```bash
cp .env.example .env             # macOS / Linux: the same, then
docker compose up -d --build
curl -s http://localhost:8000/health
```

## What's inside

- **One package of facts per trip day**: the itinerary, the trip's preferences, time zone,
  weather, places, travel times and anything that looks off, gathered once.
- **Three writing agents**: the Summariser and the Surprise finder work in parallel, then the
  Composer puts the brief together.
- **A judge, with code having the final say**: rule checks first, then a score and a list of
  anything serious. Code decides whether to send, fix once, or fall back to a plain version built
  only from facts. The traveller never gets nothing.
- **Checks on every line a model writes**: no invented times, no places we haven't verified, no
  promises about safety, and no outdoor ideas in the rain or the midday heat.
- **Evaluation**: a fixed set of 22 trip days, one change at a time, a scorecard, checks on the
  judge itself, cost reports, and a blind review pack for Travezy's team.
- **The service**: FastAPI with Postgres, a batch runner that prepares briefs before each
  traveller's local morning, a phone-style preview, a kill switch and a monthly budget with alerts and a hard stop.

## Folders

| Folder | What you'll find |
|---|---|
| `config/` | Which model does what, sources, rubrics, prices, app settings, the test set |
| `mb/` | The application: contracts, context, sources, agents, judge, pipeline, API, batch, evaluation |
| `prompts/` | Every prompt version, one file each |
| `data/` | Sample itineraries, trips, preferences and weather scenarios |
| `tests/` | Automated tests (no network, no cost) |
| `docs/` | Setup, design note, architecture and build log |

Every paid call goes through a budget guard and is logged.

## Earlier work

Brief 1, the first project in this process:
[travezy-insight-query](https://github.com/jahnvitravezy/travezy-insight-query).
