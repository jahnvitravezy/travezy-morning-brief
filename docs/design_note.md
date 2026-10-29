# Morning Brief: design note

Brief 6 for Travezy. This note explains how the Morning Brief is built, why each decision was
made, what it costs, and what still has to be decided before launch.

## 1. What it does

Each morning of a trip, the traveller gets one notification: their day in one line, anything
they need to know (weather, closing times, clashes), and two or three things they could add in a
free window. Tapping it opens the full brief.

The service builds the brief and says when to send it. Travezy's backend sends the notification.

## 2. The rule behind everything

**Code computes the facts; the model writes the words.**

Times, travel minutes, opening hours, weather clashes, spend and the send time are calculated in
code from the sources. The models only choose wording and pick from lists that code has already
checked. This is why a brief cannot invent an opening time or a travel time, and why the judge
is told which facts came from code and must not be disputed.

## 3. Architecture

See `docs/architecture.html` for the diagram. In the four simple layers:

| Layer | What sits there |
|---|---|
| **Data** | Itinerary and trip preferences (one loader file), weather (Open-Meteo), Google Places, Google Routes, Tavily search, the cache, the context layer (`ctx_id`) |
| **Agents** | Summariser, Surprise finder (run in parallel), Composer |
| **Checks** | Rule checks in code, the judge (Agent 4), one fix attempt, safe fallback, serious-error rule |
| **Output** | The output contract (`MorningBrief`), send time in UTC, the outbox in Postgres, the API, the web preview |

In the six detailed layers:

| Layer | What sits there |
|---|---|
| Data | Itinerary loader, preferences, weather, places, routes, web search, cache |
| Knowledge | Context layer: one package of facts per trip day, content-addressed by `ctx_id`; candidate rules; source IDs on every fact |
| Capability | Model gateway (LiteLLM + Instructor), registry (`config/agents.yaml`, `models.yaml`, `sources.yaml`), prompts with versions, timeouts and retries |
| Intelligence | Summariser, Surprise finder, Composer |
| Experience | Output contract, notification title and body, full brief, web preview, send time |
| Governance | Judge and rubric, evaluation framework and scorecard, cost and speed tracking, budget cap, kill switch, notification controls, privacy |

## 4. The four agents

| Agent | Job | Why it is separate |
|---|---|---|
| Summariser | Headline, heads-ups, weather advice, tip | Reads the whole day; the timeline and spend are computed in code and passed in |
| Surprise finder | Picks 2–3 ideas for a free window | Chooses only from candidates that code gathered and filtered (open, near, fits the window, fits the diet and pace) |
| Composer | Merges both into the output contract and writes the notification | Removes duplicates and clashes, keeps within length limits, makes sure every must-report fact is covered |
| Judge | Scores the draft against the rubric | Gives a reason for every score; code, not the judge, decides the outcome |

Agents 1 and 2 run in parallel. Every agent has a timeout and a retry. If anything fails, a safe
version built from code is sent instead: **the service never sends nothing.**

## 5. Checks and the decision rule

1. **Rule checks in code first**: length limits, required sections, the number of ideas, that
   every idea has a source, and that every must-report fact (weather clash, closing time) is in
   the brief.
2. **The judge scores each criterion 0–100** against fixed anchors written in the rubric, with
   a reason, and lists any **serious errors** (a wrong opening time, a diet mistake, anything
   unsafe), quoting the brief and the input it contradicts.
3. **Code confirms each serious error** before it can fail the brief: it must quote the brief
   word for word, the quote must really be in the brief, and it must not be about a fact code
   computed or on the never-serious list (for example "check the hours" for a venue that hasn't
   published them). An unconfirmed flag is kept as an issue for the fix, not as a failure. This
   came from a live false alarm on 29 Oct, where one unquoted flag sent a good brief to the
   safe version.
4. **Code decides:**
   - any serious error → the brief fails, whatever its score;
   - score ≥ green (85) and no rule failures → send;
   - otherwise → fix once, re-judge, keep the fix only if it is not worse;
   - still failing → send the safe fallback.
5. A fixed brief is always re-checked before it is sent.

The thresholds (85 green, 60 amber) are placeholders until they are set against Travezy's
reviewers. A second rubric (`morning_brief_launch.v1`, green 90, amber 70, stricter lengths and
weights) shows that rubrics are swapped in config, not code: `--rubric` on the judge run.

## 6. Evaluation framework

- **Fixed test set**: 22 trip days with stored contexts, so every model sees the same facts.
- **One change at a time**: compare one role against GPT-4o, with the judge held fixed.
- **First score**: models are compared on the score before any fix.
- **Noise first**: the judge's own noise is about ±2 points, so differences smaller than that are
  treated as ties.
- **Preflight and a validity column**: every run checks keys, models and settings first, and a
  run with failed calls is marked invalid rather than counted.
- **Self-preference test**: the judge did not favour briefs written by its own model family
  (+0.4, +0.5, +3.0 points).

Results: GPT-4o scores 88.3 on average. Every valid alternative was within 2.4 points. GPT-4.1
mini is the cheaper candidate for the Surprise finder. An all-Sonnet pipeline gave the same
quality but was 3× slower and 3.7× the cost. Haiku as Composer often wrote bodies that were too
long, so it fell back more often. Full numbers are in the scorecard and `docs/build_log.md`.

**Choosing the production judge**: Travezy's team scores the 20 briefs blind with a stronger
reviewer. `python -m mb.evals.agreement` compares each of our judges with those verdicts
(agreement, weighted kappa, unsafe briefs caught, false alarms, best cut-offs), and the judge
that agrees best becomes the production judge. Until then, stand-in reviewers from two model
families (`mb.evals.proxy_review`) can run the same comparison; they are labelled stand-ins and
do not replace the real review.

## 7. Notification controls

| Control | How it works |
|---|---|
| One per day | The outbox is keyed by date and trip; a second run does not create a second brief (`--force` to regenerate) |
| Send window | 07:30 local, or 60 minutes before the first activity if earlier (`config/app.yaml`) |
| Time zone changes | The send time uses the time zone of where the traveller wakes up that day, then converts to UTC |
| Quiet hours | Nothing is scheduled before the send window; Travezy's backend applies the user's own quiet-hours setting |
| Opt-out per trip | Travezy's backend owns the user's setting and simply doesn't request or send a brief for a trip that has opted out |
| Dedupe | Idempotent outbox with an upsert and an advisory lock, so two workers cannot write the same brief |
| Kill switch | `briefs_enabled: false` in `config/app.yaml` or `MB_KILL_SWITCH=1`: the API returns 503 and the batch skips |
| Cost caps | A budget guard checks estimated spend before every paid call and refuses to go past the cap; every call is logged |

## 8. Privacy

- Only what the brief needs is read: the day's itinerary, the trip's preferences and the wake
  location. The loader takes no traveller names, emails or phone numbers, so none reach a model;
  keep it that way when the API loader is added.
- Keys live only in `.env`, never in code, images or logs.
- Cached lookups hold place and weather data, not personal data.
- The review pack hides model names and internal IDs.
- Before launch: agree a retention period for the outbox and logs, and check each provider's
  data-use terms (no training on API data).

## 9. Cost and speed

| | Per brief |
|---|---|
| Models (four agents, GPT-4o + GPT-4o-mini judge) | ≈ ₹1.13 |
| Data (Places, Routes, Tavily; measured in the batch run, with cache) | ≈ ₹11.1 (₹15.6 cold) |

Google gives 1,000 Text Search calls a month free (≈ ₹3,000) and 10,000 Route Matrix elements.
Prices were checked on 29 Oct 2026 (`config/prices.yaml`). Monthly cost at 50 and 100
travellers a day is in `review/cost_and_time.md`. The biggest saving is the data side: caching
places across travellers on the same route and fetching only for free windows.

### Exchange rates

The traveller's spend line ("about ₹5,032 per person") uses a live rate, never a number typed
into config: today's rate from Exchange Rate API's free daily endpoint, fetched once a day and
cached. If it can't be reached, the most recent rate up to 2 days old is used; older than that,
the brief shows the local amount only and drops the home-currency figure. The free endpoint
requires the "Rates By Exchange Rate API" credit (shown in the preview) and may not be
redistributed; for launch, the cleaner choice is the rate source Travezy's checkout already uses.
Tests and evaluation runs use fixed rates (`MB_FX=fixed`) so results are repeatable. The cost
report's ₹ figures use the dated rate in `config/app.yaml`.

## 10. Production handover

- Separate Python/FastAPI service, one command to run: `docker compose up -d --build`.
- Keys out of code (`.env`, see `.env.example`).
- **One loader file to swap**: `mb/sources/itinerary.py` (`get_loader`). It reads files today;
  the `api` branch is where Travezy's itinerary and profile API goes.
- The API returns the brief plus `send_at_utc`; Travezy's backend sends it.
- Travezy's coding conventions to be applied once received.

## 11. Open decisions

1. Green and amber thresholds, set against Travezy's reviewer verdicts.
2. The production judge model, chosen by agreement with those verdicts.
3. The Surprise finder model (GPT-4o or the cheaper GPT-4.1 mini).
4. Retention period for briefs and logs.
5. Open-Meteo's commercial plan for production use.
