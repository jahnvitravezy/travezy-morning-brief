# Morning Brief: running it in production

Answers to the points from the 3 Nov walkthrough that are about operations rather than code.
Settings named here live in `config/`, so none of them needs a code change.

## Batch schedule (1.6)

- **What runs:** `python -m mb.batch --window 120`. It finds every traveller whose brief is due in
  the next 120 minutes, writes it and stores it in the outbox. A brief already in the outbox is
  skipped, so overlapping or repeated runs are safe.
- **How often:** every 15 minutes, all day (`*/15 * * * *`). Send times follow each traveller's
  local morning, so somewhere in the world a brief is always about to be due.
- **Who triggers it:** Travezy's scheduler (a cron job on the backend server, Kubernetes CronJob or
  Cloud Scheduler), running the service's container with the command above. A second daily job
  runs `python -m mb.db cleanup` (retention, below).
- **Delivery:** the backend reads `GET /outbox/{date}` every minute and sends each brief at its
  `send_at_utc`. Each brief has a stable `brief_id`, so a retried send is easy to de-duplicate.

## If the backend misses a send time, or the service is down at 07:00 (4.3)

- Briefs are prepared up to 120 minutes ahead, so a short outage after preparation changes nothing:
  the brief is already in the outbox.
- If the service was down while a brief was due, the next run (every 15 minutes) prepares it. The
  backend should still send a brief up to about 10:00 local; after that, skip the day.
- If a brief can't be written at all (models down, budget stop), the safe version goes out: the
  day's plan from the itinerary, no ideas, no badge. The traveller never gets nothing.

## Freshness of ideas (1.7, proposal, not built)

Briefs are written once in the morning, so an event can change after it's written. Low-cost
approach, in order of value:

1. **Prefer stable places.** Ideas come from places with published opening hours (Google Places),
   which change rarely and are refreshed every 3 days (`places_hours_refresh_days`).
2. **Label anything time-bound.** An event or show is shown as "Event, check before you go" with
   its source link, never stated as certain.
3. **Re-check time-bound ideas shortly before the slot.** About 60 minutes before an event's slot,
   one Place Details call (or the event page) confirms it's still on; if not, the app hides the
   card. Cost: one call per event idea, only for briefs that contain one.
4. **Let the traveller report it.** A "this was closed" tap feeds back into the places table.

## Range of ideas (1.8)

| Idea type | Example | Verified by | Shown as |
|---|---|---|---|
| Place | museum, park, cafe, viewpoint | Google Places: place ID, opening hours for that slot, rating, travel time | Fact: open, distance, slot |
| Food | restaurant, dessert bar | Google Places, plus diet fit (types such as vegetarian_restaurant) | Fact, with diet note |
| Event or exhibition | festival, gallery exhibition | Web search (Tavily) with a source link; dates read from the page | "Event, check before you go" |
| Show | concert, theatre | Web search with a source link; later a ticketing source (Viator, which plugs in through the same provider interface) | "Event, check before you go" |

Today the Surprise finder uses places and food (Google Places) and one web search per day for
events. Every idea must come from this verified list; the model can't add its own.

## Scaling (1.10)

| Travellers a day | What happens today | What breaks | What I'd change |
|---|---|---|---|
| 100 | One batch every 15 min, 3 briefs at a time, about 8 s each | Nothing | Nothing |
| 1,000 | Briefs cluster around 07:30 in each time zone: up to 300 due in one window | 300 × 8 s ÷ 3 at a time = about 13 minutes, inside the 120-minute window, but close to provider rate limits at peak | Raise `--concurrency` to 10, prepare 3 hours ahead instead of 2, and spread calls across the window |
| 10,000 | One batch process | One process can't keep up, and two cron runs would duplicate work | A Postgres job queue: each due brief becomes a row, several workers claim rows with `FOR UPDATE SKIP LOCKED`. Use the provider's batch API for briefs prepared hours ahead (about half the model price). Shared cache and places table already work across workers |

**Delivery:** FastAPI stays on both sides; what changes with volume is the delivery pattern.

- **Today, polling:** the backend reads the outbox every minute. Simple and reliable, but a brief
  can wait up to a minute and the backend asks even when nothing is new.
- **Up to about 1,000 a day, a webhook:** when a brief is ready, the service calls an endpoint on the
  backend (`POST /morning-brief`, with the API key and `brief_id`), retrying with backoff.
- **10,000 a day and beyond, a message queue** (Pub/Sub, SQS or Redis Streams, whichever the cloud
  already uses): the service publishes each ready brief and a worker on the backend consumes it. The
  queue absorbs the 07:30 bursts, retries by itself and lets either side be down for a while.

In every case the outbox stays the record and `brief_id` is the idempotency key, so a brief
delivered twice is sent once. Built so far: only `--concurrency`; the queue and the push are a plan,
not load-tested, and real limits depend on the OpenAI account tier.

## Keys (1.11 and 2.3)

The service uses its own keys, separate from Travezy's product keys, so its usage and bills are
separate and either can be rotated alone. Production needs only these:

| Key | For |
|---|---|
| `OPENAI_API_KEY` | The three writing agents and the judge |
| `GOOGLE_MAPS_API_KEY` | Places and Routes |
| `WEATHERAPI_KEY` | Weather |
| `TAVILY_API_KEY` | Web search for events |
| `MB_API_KEY` | What Travezy's backend sends to call the service |
| `POSTGRES_PASSWORD` / `DATABASE_URL` | The database, from Travezy's secrets, no default |

`ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are only for model comparisons, not production.

## What Travezy's team would change to run it (4.1 and 4.2)

1. **The loader:** `mb/sources/itinerary.py`, the `api` loader, reading the fields in
   `docs/loader_spec.md`. Set `itinerary_loader: api` in `config/app.yaml`.
2. **Keys and settings:** the keys above in secrets; `DATABASE_URL` and `MB_DB_SCHEMA` for their
   database (`python -m mb.db` creates the schema and tables).
3. **The schedule:** the two cron jobs above.

Nothing else changes. On staging: about half a day, assuming the itinerary API is reachable, most
of it on the loader and secrets. With the file loader (sample trips), it runs in minutes:
`docker compose up -d --build`.

## Retention (4.4)

`python -m mb.db cleanup`, run daily, deletes rows past `retention_days` in `config/app.yaml`:

| What | Kept | Why |
|---|---|---|
| Briefs (outbox) | 30 days | Support questions about a recent brief |
| Cache (weather, routes, search) | 30 days | Stale after that anyway |
| Searches for places | 30 days | Repeated when older |
| Places table | Kept, refreshed every 3 days | Reused across travellers |
| Paid-call ledger | 400 days | Invoices and the monthly budget |
| Logs | 30 days (log platform setting) | No names, emails or phones are ever logged |

## Coding conventions (4.5)

Within two working days of receiving them.
