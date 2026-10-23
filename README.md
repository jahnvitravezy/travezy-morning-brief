# Travezy Morning Brief

Travezy's first multi-agent feature (Brief 6). Each morning of a trip, the traveller gets one
notification with their day, plus two or three things they could add to it. Four agents build
the brief, an AI judge scores it, and an evaluation framework compares models for production.

> Work in progress. The full setup guide and design note are added at the end of the build.

## What runs today

- **Context layer:** one package of facts per trip day (itinerary, trip preferences, time zone,
  weather, Google Places, travel times, data-quality flags), built once and identified by a `ctx_id`.
- **Agent 1, Summariser:** headline, heads-ups, weather advice and tip. Timeline and spend are computed in code.
- **Agent 2, Surprise finder:** picks only from candidates gathered and filtered in code.
- **Agent 3, Composer:** merges everything into the output contract.
- **Runner:** Agents 1 and 2 in parallel, timeouts, retries, a time budget and fallbacks (never sends nothing).
- **API:** `POST /brief`, `GET /health`, `GET /trips`; contract at `/docs`.

Coming next: the judge (Agent 4), the test set, the evaluation framework and the batch runner.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest -q
```

Keys go in a `.env` file in the project root (never committed):

```
OPENAI_API_KEY=...
GOOGLE_MAPS_API_KEY=...
TAVILY_API_KEY=...
```

Run the service in Docker:

```powershell
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/health
```

Then open http://localhost:8000/docs.

## Layout

| Folder | What's in it |
|---|---|
| `config/` | Agent registry, sources, models, rubric, prices, budget and candidate rules |
| `mb/` | The application: contracts, context, sources, agents, pipeline, API |
| `prompts/` | Versioned prompts, one file per version |
| `data/` | Sample itineraries, trip preferences, weather scenarios |
| `tests/` | Automated tests, no network or cost |
| `docs/` | Build log |

Model and API usage is capped at INR 5,000 by a budget guard; every paid call is logged.
