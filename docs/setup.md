# Morning Brief: setup guide

This is everything you need to run the service, the tests and the evaluation on a fresh machine.
Commands are shown for Windows PowerShell and for macOS or Linux (bash or zsh).

## 1. What you'll need

- Docker Desktop, running
- Python 3.12, only if you want to run the tests or the evaluation scripts
- API keys: OpenAI (required), Google Maps Platform with Places and Routes switched on, and Tavily.
  Anthropic and Gemini keys are only needed if you want to try those models.

## 2. Add your keys

```powershell
Copy-Item .env.example .env      # Windows
notepad .env
```

```bash
cp .env.example .env             # macOS / Linux
nano .env
```

Fill in the keys and save. Also set:
- `MB_API_KEY`: any long random string. Every call must send it as the `X-API-Key` header.
- `POSTGRES_PASSWORD`: required, with no default. Production reads it from secrets.

To make a random string: PowerShell `-join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_})`, or on macOS and Linux `openssl rand -hex 16`. Git ignores `.env`, and it's never copied into the Docker image, so
your keys stay on your machine.

## 3. Start the service

```powershell
docker compose up -d --build
```

That's the one command. It starts the API and a Postgres database for the outbox. Give it 15
seconds to start up, then check it's alive:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

```bash
curl -s http://localhost:8000/health
```

`/health` needs no key. Every other endpoint needs the `X-API-Key` header:

```bash
curl -s -H "X-API-Key: $MB_API_KEY" http://localhost:8000/trips
```

On start-up the service creates its tables in their own schema (`MB_DB_SCHEMA`, default
`morning_brief`). To run the migrations yourself, use `python -m mb.db`.

| Endpoint | What it does |
|---|---|
| `GET /health` | Tells you the service is up |
| `GET /trips` | Lists the sample trips and their dates |
| `POST /brief` | `{"trip_id": "dubai_up01", "date": "2026-11-21"}` returns the brief and `send_at_utc` |
| `POST /judge` | Scores a brief against the rubric |
| `GET /outbox/{date}` | Shows the briefs prepared for that date |
| `GET /preview/{date}/{trip_id}` | Shows a prepared brief the way it looks on a phone |

The full API is described at http://localhost:8000/docs.

Try it:

```powershell
$body = @{ trip_id = "dubai_up01"; date = "2026-11-21"; weather_scenario = "dubai_clear_warm" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/brief -Method Post -Body $body -ContentType "application/json"
```

The sample trips are in late 2026, beyond the 16-day weather forecast, so `weather_scenario`
gives the brief stored weather to work with. A real trip a day ahead uses the live forecast.

To stop everything, run `docker compose down`. The prepared briefs are kept in the `pgdata` volume.

## 4. Preparing tomorrow's briefs

The batch runner finds every traveller whose brief is due soon, writes it and stores it in the
outbox:

```powershell
docker compose run --rm api python -m mb.batch --plan          # who's due, at no cost
docker compose run --rm api python -m mb.batch --window 60     # prepare them
```

Running it twice is safe: a brief that's already in the outbox is skipped. Once briefs are
prepared, you can see one with `Start-Process http://localhost:8000/preview/<date>/<trip_id>`.

In production this would run on a schedule, say every 15 minutes, and Travezy's backend would
read the outbox and send each brief at its `send_at_utc`.

## 5. The kill switch

To stop all new briefs at once, set `briefs_enabled: false` in `config/app.yaml`, or add
`MB_KILL_SWITCH=1` to `.env`, then run `docker compose up -d`. The API answers new requests with
503 and the batch skips its run. Undo the change to switch it back on.

## 6. Running the tests

The tests need no network and cost nothing.

```powershell
python -m venv .venv                 # Windows
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest -q
```

```bash
python3 -m venv .venv                # macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

The Postgres tests run only when `TEST_DATABASE_URL` points at a throwaway database.

## 7. Running the evaluation

Every paid call goes through the budget guard (the cap is in `config/app.yaml`) and is logged in
`data/usage/`. Set `MB_FX=fixed` first so the results can be repeated.

On macOS and Linux, use `export MB_FX=fixed` instead of the first line.

```powershell
$env:MB_FX = "fixed"
python -m mb.evals.runner baseline                                          # all 22 test days, current models
python -m mb.evals.runner cmp-x --set summariser=openai/gpt-4.1-mini        # change one role
python -m mb.evals.judge_run baseline --model anthropic/claude-haiku-5-5     # a different judge, same briefs
python -m mb.evals.judge_run baseline --rubric rubric.morning_brief_launch.v1
python -m mb.evals.scorecard                                                # data/runs/scorecard.html
python -m mb.evals.cost_report baseline                                     # cost and time per brief
python -m mb.evals.blinded_pack baseline                                    # the 20-brief pack
python -m mb.evals.review_export baseline                                   # the blind review files
python -m mb.evals.agreement baseline reviewer.xlsx                         # our judges against a reviewer
```

### Asking questions about the runs in plain English

Every run's scores, verdicts, response time and tokens can be queried through the Brief 1 insight
tool ([travezy-insight-query](https://github.com/jahnvitravezy/travezy-insight-query)). Write the
table here, then load it there:

```powershell
python -m mb.evals.export_runs                       # data/runs/brief_runs.csv, one row per brief per run
cd ..\travezy
python scripts/load_morning_brief.py                 # into analytics.brief_runs
python scripts/ask_cli.py "Which model failed most often on arrival days?"
```

A brief counts as failed if the traveller got the safe version, an agent failed, or a rule check
failed. The Gemini runs are marked invalid (free-tier quota, so the model was never really tested)
and left out of what the tool reads.

## 8. Where to change things

| To change | Edit |
|---|---|
| Which model does a job | `config/agents.yaml` (the models are listed in `config/models.yaml`) |
| A prompt | Add a new file in `prompts/` and point the agent at it |
| The rubric, pass marks or weights | `config/rubrics/` |
| The send time, monthly budget and alerts, kill switch, retention, weather provider | `config/app.yaml` |
| Timeouts and retries for every agent and API | `config/limits.yaml` |
| A model's end-of-life date | `config/models.yaml` (warned 90 days ahead in the log and on `/health`) |
| Prices | `config/prices.yaml` |
| Where itineraries come from | `mb/sources/itinerary.py` (`get_loader`) and `itinerary_loader` in `config/app.yaml` |

## 9. If something goes wrong

- **"The connection was closed" straight after starting**: the API is still starting up. Wait 15
  seconds and try again.
- **`docker compose run` seems to use old code**: run `docker compose build` first.
- **The build stops with a timeout**: it's usually the internet connection. Run the build again;
  downloaded packages are kept, so it picks up where it left off.
- **A run is marked invalid**: the check before the run found a missing key, a quota limit or a
  setting the model doesn't accept. The reason is in the run's records.
- **"Budget reached"**: the guard has stopped new paid calls. Only raise `monthly_budget_inr` once
  that's been agreed.
