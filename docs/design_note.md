# Morning Brief: design note

This note explains how the Morning Brief works, why I made each decision, what it costs to run,
and what still needs deciding before launch. It's for anyone reviewing the service or taking it over.

## 1. What it does

On every morning of a trip, the traveller gets one notification. It tells them their day in a
line, anything they need to know before they go (weather, closing times, a booking to be on time
for), and two or three things they could add if they have a free gap. Tapping it opens the full
brief.

This service writes the brief and works out when it should arrive. Travezy's own backend sends
the notification.

## 2. The idea behind everything

**Code works out the facts. The models only write the words.**

Every time, travel time, opening hour, weather clash, cost and send time is calculated in code
from real sources. The models choose how to say things, and they pick ideas only from a list that
code has already checked. That's why a brief can't invent an opening time or a travel time. It's
also why the judge is told which facts came from code: it isn't allowed to argue with them.

Most of what I learned building this comes back to that one rule. Whenever a model got something
wrong, the fix was almost always to take that fact away from the model and calculate it in code.

## 3. How it fits together

The diagram is in `docs/architecture.html`. In Travezy's four layers:

| Layer | What sits there |
|---|---|
| **Data** | The itinerary and the trip's preferences (read through one loader file), weather from Open-Meteo, Google Places and Routes, Tavily search, the cache, and the day's package of facts (`ctx_id`) |
| **Agents** | The Summariser and the Surprise finder, which run side by side, then the Composer |
| **Checks** | Rule checks in code, the judge (agent 4), one attempt at a fix, the safe version, the serious-error rule |
| **Output** | The brief itself (`MorningBrief`), the send time in UTC, the outbox in Postgres, the API, the web preview |

And in the six detailed layers:

| Layer | What sits there |
|---|---|
| Data | Itinerary loader, preferences, weather, places, routes, web search, cache |
| Knowledge | One package of facts per trip day, identified by its content (`ctx_id`); the rules for choosing ideas; a source on every fact |
| Capability | The model gateway (LiteLLM and Instructor), the registry of agents, models and sources, versioned prompts, timeouts and retries |
| Intelligence | Summariser, Surprise finder, Composer |
| Experience | The brief, the notification title and text, the full brief, the preview, the send time |
| Governance | The judge and rubric, the evaluation and scorecard, cost and speed tracking, the budget cap, the kill switch, notification controls, privacy |

## 4. The four agents

| Agent | What it does | Why it's its own agent |
|---|---|---|
| Summariser | Writes the headline, the heads-ups, the weather advice and a local tip | It reads the whole day. The timeline and costs are worked out in code and handed to it |
| Surprise finder | Picks two or three ideas for a free gap | It can only choose from places code has already found and checked: open, close enough, fits the gap, suits the diet and the pace |
| Composer | Puts everything together and writes the notification | It removes repeats and clashes, keeps to the length limits, and makes sure nothing that must be said is missing |
| Judge | Scores the draft against the rubric | It gives a reason for every score, but code, not the judge, decides what happens next |

The first two run at the same time. Every agent has a time limit and one retry. If anything goes
wrong, the traveller gets a plain version built only from code facts. **The service never sends
nothing.**

## 5. Checks, and who decides

1. **Rule checks run first, in code.** Length limits, every section present, the right number of
   ideas, a source for every idea, and every must-say fact (a weather clash, a closing time)
   actually in the brief.
2. **The judge scores five things from 0 to 100** against fixed descriptions in the rubric, with a
   reason for each, and lists anything **serious**: a wrong time, a diet mistake, anything unsafe.
   It has to quote the line in the brief and the input it contradicts.
3. **Code checks every serious flag before it counts.** The flag has to quote the brief word for
   word, the quote has to really be there, and it can't be about something code worked out or
   something on the "never serious" list (like "check the hours" for a place that doesn't
   publish them). A flag that fails this is kept as something to fix, not as a failure. I added
   this after a live test on 29 Oct, when one false flag sent a perfectly good brief to the safe
   version.
4. **Code decides:**
   - any serious error, and the brief fails, whatever it scored;
   - 85 or more with no rule failures, and it's sent;
   - anything else gets one fix, then it's judged again, and the fix is kept only if it isn't worse;
   - still failing after that, and the traveller gets the safe version.
5. A brief that's been fixed is always checked again before it goes out.

### Checks on everything a model writes

Before the judge sees a brief, code checks every line a model wrote. Each of these checks exists
because a real mistake turned up in review:

| Check | What it stops | Where it came from |
|---|---|---|
| Times | A heads-up or tip can only mention a time from the plan, the free gaps, the send time or something code wrote. Any other time, and the line is dropped | "Leave Kelingking by 11:16" (it should have been 11:46) |
| Must-say facts | Closing times, clashes, diets, steep or uneven paths, cash-only, timed entries, airport transfers. If the model leaves one out, code adds it back | dropped warnings, 25 Oct |
| Tips | No place outside the plan or the ideas, no risky advice, no safety promises ("safe for children"), no claims nothing confirms ("best views at sunset") | the stand-in review |
| Ideas | Only checked places. Outdoor ideas only in dry, not-too-hot hours. Each one ends 15 minutes before they need to leave for the next stop or the hotel. If the description oversells the place, it's replaced with plain facts | a water park at 38°C; an aquarium visit running into the airport taxi |
| Counts | The number of ideas in the notification is set by code | "plus 0 ideas for you" |

### What each review round showed (stand-in reviewer, same 20 briefs)

| Run | Reviewer: "send as is" | Reviewer: "wrong or unsafe" | Judge agreement with the reviewer (kappa) |
|---|---|---|---|
| v5 (the pack sent to Travezy) | 9 | 4 | 55% (0.21) |
| v6 | 10 | 3 | 50% (−0.03) |
| v7 | 9 | 7 | 70% (0.40) |
| v8 | 15 | 3 | 75% (0.44) |

v7 got worse before it got better: the models had started copying the "Leave X by…" style with
times they made up. The time check fixed that. In v8 there were no agent failures and every rule
check passed. Its one fallback was the right call: after the fix step, a model wrote a travel time
wrongly, the judge caught it as serious, and the traveller got the safe version. The stand-in
reviewer also varies from run to run, so I read these numbers as a trend, not as proof. Travezy's
own reviewer has the final word.

The pass marks (85 for green, 60 for amber) are placeholders until they can be set against
Travezy's reviewer. A second rubric (`morning_brief_launch.v1`: green at 90, amber at 70, tighter
lengths, different weights) shows that rubrics live in config, not code. Pass `--rubric` to the
judge run to use it.

## 6. How models are evaluated

- **The same exam every time**: 22 trip days with their facts stored, so every model sees exactly
  the same thing.
- **One change at a time**: one role is swapped against GPT-4o, and the judge stays the same.
- **The first score**: models are compared on their score before any fix, so the fix step can't
  hide a weaker model.
- **Noise first**: the judge's own score moves by about 2 points when it re-scores the same brief,
  so anything closer than that is a tie.
- **Check before trusting a run**: every run checks keys, models and settings first, and a run
  where calls failed is marked invalid instead of counted. I learned this the hard way when the
  first comparison runs quietly tested nothing.
- **Does the judge favour its own family?** No: +0.4, +0.5 and +3.0 points, all within its own
  noise.

What the comparison found (judge-v2): GPT-4o averaged 88.3, and every valid alternative came
within 2.4 points of it. GPT-4.1 mini is the cheaper option worth confirming for the Surprise
finder. Claude Sonnet in every role matched the quality but was three times slower and 3.7 times
the cost. Claude Haiku as the Composer kept writing notifications that were too long. The full
numbers are in the scorecard and `docs/build_log.md`.

**Choosing the judge for production**: Travezy's team is scoring the 20 briefs blind with a
stronger reviewer. `python -m mb.evals.agreement` then compares each of our judges with their
verdicts (agreement, weighted kappa, unsafe briefs caught, false alarms, best pass marks), and the
judge that agrees best goes into production. Until those verdicts arrive, stand-in reviewers from
two model families (`mb.evals.proxy_review`) run the same comparison. They're clearly labelled as
stand-ins and don't replace the real review.

## 7. Notification controls

| Control | How it works |
|---|---|
| One a day | The outbox is keyed by trip and date, so running it again never creates a second brief (`--force` regenerates one on purpose) |
| When it arrives | 07:30 local time, or an hour before the first activity if that's earlier (`config/app.yaml`) |
| Changing time zones | The send time uses the time zone where the traveller wakes up that day, then converts to UTC |
| Quiet hours | Nothing is scheduled before the send time; Travezy's backend applies each user's own quiet hours |
| Opting out of a trip | Travezy's backend holds that setting and simply doesn't ask for or send a brief for that trip |
| No duplicates | The outbox writes safely even if two workers run at once (an upsert with an advisory lock) |
| Kill switch | `briefs_enabled: false` in `config/app.yaml`, or `MB_KILL_SWITCH=1`, stops every new brief: the API returns 503 and the batch skips |
| Cost cap | A budget guard checks the likely cost before every paid call and refuses to go over the cap. Every call is logged |

## 8. Privacy

- The service reads only what the brief needs: the day's plan, the trip's preferences and where
  the traveller wakes up. The loader takes no names, emails or phone numbers, so none of them ever
  reach a model. That should stay true when the real API loader is added.
- Keys live only in `.env`, never in the code, the Docker image or the logs.
- The cache holds place and weather data, nothing personal.
- The review pack hides model names and internal IDs.
- Before launch: agree how long to keep briefs and logs, and check each provider's data terms (no
  training on our API data).

## 9. Cost and speed

| Per brief | Cost |
|---|---|
| Models (four agents, GPT-4o writing, gpt-4o-mini judging, fixes included) | about ₹1.22 |
| Data (Places, Routes, Tavily), measured in the batch run with some caching | about ₹12 (about ₹17 with nothing cached) |

A brief takes about 9 seconds to write (16 seconds at the slow end), and the batch prepares them
well before the send time. At 1 USD = ₹95.22 (checked 30 Oct). Google's free monthly allowances
(1,000 Text Search calls, about ₹3,333, and 10,000 Route Matrix elements, about ₹4,761) aren't
deducted. Prices were checked against Google's pricing page on 29 Oct 2026 (`config/prices.yaml`).
The monthly cost at 50 and 100 travellers a day is in `cost_and_time.md` in the review pack.

Data is most of the cost, so that's where the saving is: travellers on the same itinerary share
hotels and sights, and a cache shared between them removes most repeat lookups.

### Exchange rates

The spend line ("about ₹5,032 per person") uses a live rate, never a number typed into a file. It
fetches today's rate from Exchange Rate API's free daily feed once a day and caches it. If the feed
is down, it uses the latest rate up to 2 days old. If there's nothing that recent, the brief shows
the local amount only and leaves out the rupee figure rather than guess. The free feed needs a
small "Rates By Exchange Rate API" credit (shown in the preview), and its data can't be passed on.
For launch, the simpler choice is whatever rate Travezy's checkout already uses. Tests and
evaluation runs use fixed rates (`MB_FX=fixed`) so their results can be repeated. The rupee figures
in the cost report use the dated rate in `config/app.yaml`.

## 10. Handing it over

- It's a separate Python and FastAPI service that runs with one command: `docker compose up -d --build`.
- Keys stay out of the code, in `.env` (see `.env.example`).
- **One loader file to change**: `mb/sources/itinerary.py` (`get_loader`). Today it reads files;
  the `api` branch is where Travezy's itinerary and profile API plugs in.
- The API returns the brief and `send_at_utc`; Travezy's backend sends it.
- I'll apply Travezy's coding conventions as soon as I have them.

## 11. Still to decide

1. The green and amber pass marks, set against Travezy's reviewer.
2. Which judge goes into production, chosen by how well it agrees with that reviewer.
3. The Surprise finder model: GPT-4o, or the cheaper GPT-4.1 mini.
4. How long to keep briefs and logs.
5. A commercial plan for Open-Meteo in production.
6. Meal and rest times: ideas don't yet avoid a family's lunch or rest break.
