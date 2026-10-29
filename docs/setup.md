# Morning Brief: setup guide

Everything needed to run the service, the tests and the evaluation on a new machine.
Commands are for Windows PowerShell; on macOS or Linux, use `source .venv/bin/activate`.

## 1. What you need

- Docker Desktop (running)
- Python 3.12 (only for tests and the evaluation scripts)
- API keys: OpenAI (required), Google Maps Platform with Places and Routes enabled, Tavily.
  Anthropic and Gemini are only needed to try those models.

## 2. Keys

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in the keys. `.env` is ignored by git and is never copied into the Docker image.

## 3. Run the service (one command)

```powershell
docker compose up -d --build
```

This starts the API and a Postgres database for the outbox. Wait a few seconds, then:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

| Endpoint | What it does |
|---|---|
| `GET /health` | Service is up |
| `GET /trips` | Sample trips and their dates |
| `POST /brief` | `{"trip_id": "dubai_up01", "date": "2026-11-21"}` → the brief and `send_at_utc` |
| `POST /judge` | Score a brief against the rubric |
| `GET /outbox/{date}` | Briefs prepared for that date |
| `GET /preview/{date}/{trip_id}` | A prepared brief as a phone-sized web page |

The full contract is at http://localhost:8000/docs.

Example:

```powershell
$body = @{ trip_id = "dubai_up01"; date = "2026-11-21" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/brief -Method Post -Body $body -ContentType "application/json"
Start-Process http://localhost:8000/preview/2026-11-21/dubai_up01
```

Stop with `docker compose down` (briefs are kept in the `pgdata` volume).

## 4. The batch runner

Prepares every brief due in the next window and stores it in the outbox:

```powershell
docker compose run --rm api python -m mb.batch --plan          # who is due, no cost
docker compose run --rm api python -m mb.batch --window 60     # prepare them
```

In production this runs on a schedule (for example every 15 minutes); Travezy's backend reads
the outbox and sends each brief at its `send_at_utc`.

## 5. Kill switch

Set `briefs_enabled: false` in `config/app.yaml`, or `MB_KILL_SWITCH=1` in `.env`, then
`docker compose up -d`. The API returns 503 for new briefs and the batch skips.

## 6. Tests (no network, no cost)

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest -q
```

## 7. Evaluation

Every paid call goes through the budget guard (cap in `config/app.yaml`) and is logged in
`data/usage/`.

```powershell
python -m mb.evals.runner baseline                                          # 22 test days, registry models
python -m mb.evals.runner cmp-x --set summariser=openai/gpt-4.1-mini        # change one role
python -m mb.evals.judge_run baseline-v5 --model anthropic/claude-haiku-5-5  # another judge, same briefs
python -m mb.evals.judge_run baseline-v5 --rubric rubric.morning_brief_launch.v1
python -m mb.evals.scorecard                                                # data/runs/scorecard.html
python -m mb.evals.cost_report baseline-v5                                  # cost and time per brief
python -m mb.evals.review_export baseline-v5                                # the blind review pack
python -m mb.evals.agreement baseline-v5 reviewer.xlsx                      # judges vs the reviewer
```

## 8. Where to change things

| To change | Edit |
|---|---|
| A model for a role | `config/agents.yaml` (models listed in `config/models.yaml`) |
| A prompt | Add a new file in `prompts/` and point the agent at it |
| Rubric, thresholds, weights | `config/rubrics/` |
| Send time, budget, kill switch | `config/app.yaml` |
| Prices | `config/prices.yaml` |
| Where itineraries come from | `mb/sources/itinerary.py` (`get_loader`) and `itinerary_loader` in `config/app.yaml` |

## 9. Common problems

- **"Connection closed" right after `up`**: the API is still starting; wait a few seconds.
- **`docker compose run` uses old code**: run `docker compose build` first.
- **A model run marked invalid**: the preflight found a missing key, quota or unsupported
  setting; the error is in the run's records.
- **Budget reached**: the guard refuses new paid calls; raise `budget_inr` only after agreeing it.
