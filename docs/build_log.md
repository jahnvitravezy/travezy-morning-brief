# Build log

## 22 Oct: Summariser v1 → v2
Summariser v1 manual check: about 55/100. Missed the vegetarian clash, the museum's timed entry, picked the wrong weather clash (rice terrace instead of Monkey Forest) and named the wrong city (Denpasar). Fixed in v2 by moving time alignment into code (weather per activity), listing must-mention items in code, and making preference conflicts an explicit first field.

## 22 Oct: API keys committed by mistake
A local `api keys.txt` was committed by a broad `git add .` (Tavily commit) and pushed to the private repo. Rotated all three keys (OpenAI, Google, Tavily), removed the file from the whole history with `git filter-repo`, force-pushed, and added `*.txt` to `.gitignore`. From now on files are staged by name, not with `git add .`.

## 22 Oct: Dependencies pinned
A LiteLLM release imported a package (`mcp`) it didn't install. Pinned every version in `requirements.txt` so a fresh clone installs exactly what was tested. (PowerShell's `>` writes UTF-16; the file is saved as ASCII so pip and Docker can read it.)

## 22 Oct: Summariser v2 → v3
v2 manual check: about 78/100. Fixed the vegetarian clash, timed entry, region name and steep paths, but still picked an indoor cafe as the weather clash. Outdoor classification and clash detection moved into code (`is_outdoor`, `weather_clashes`); the model may only word a clash that code found. v3 check: about 88/100. Correct clash (Monkey Forest 16:00, 85% rain), second-person voice. Remaining: the tip sometimes overlaps a heads-up; the judge's tone criterion should catch it.

## 23 Oct: Summariser v3 → v5, and the first repeatable check
v4 added code-computed dry windows and pace checks (late dinner, late finish) and action-style heads-ups. Bali improved, but on Dubai the vegetarian kebab clash disappeared in a single run: the same model on the same day gave a different answer. Two changes followed. v5 makes the model return a fits_diet verdict for every meal before writing anything else. And `scripts/check_summariser.py` replaced judging by eye: it runs each day three times against 18 fixed checks. v5 scored 54/54 (100%) across three runs per day, for about $0.04.
Caveat: two days and checks I wrote myself. The judge over the full test set, and the team's blind scores, are the real measure.

## 23 Oct: Commit author changed
GitHub wasn't counting commits because the author email belonged to another account. Set the repo's `user.email` to `jahnvi.eval@travezy.ai` and rewrote the history with a mailmap so all commits carry that author. Force-pushed once; no code changed.

## 25 Oct: Test set, rule checks, evaluation runner, blinded pack
- **Test set** (`config/testsets/morning_brief.v1.yaml`): 22 fixed trip days over 10 trips (5 traveller profiles × Dubai and Bali). Each day lists the cases it covers; a test fails if any required case (arrival, departure, free day, weather clash, closure, booking, diet, pace, family, no surprise) is missing, or a label disagrees with the itinerary.
- **Model line-up** (`config/models.yaml`): GPT-4o as the baseline, plus GPT-4.1 mini, Claude Sonnet 5.5 and Gemini 3.8 Flash for the agents; GPT-4o mini, Claude Haiku 5.5 and Gemini 3.5 Flash-Lite for the judge.
- **Free days had no surprises**: candidates were searched around the stop before a window, and a free day has no stops. They now fall back to where the traveller woke up (the hotel), except on arrival day, when they wake up at home.
- **Judge rule checks in code** (`mb/judge/rules.py`): title/body length, max surprises, every surprise must match a place we looked up or carry a link, all fields present. Each check is named in the rubric; an unknown name stops startup instead of being skipped.
- **Runner** (`mb/evals/runner.py`): builds each day's context once and saves it by `ctx_id`, so every model is compared on the same inputs and Places is paid for once. Saves each brief plus a record (models, latency, tokens, cost, events, rule failures).
- **Blinded pack** (`mb/evals/blinded_pack.py`): 20 briefs chosen with a fixed seed so every case is still covered, coded B01–B20, shown next to their inputs, with no scores or model names. A scoring sheet uses the judge's five criteria (1–5) plus "would you send it?". The code→day key stays private.
- Tests: 107 passing.

## 25 Oct (night): Baseline reviewed, eight fixes before the pack goes out
Baseline: 22 days, 0 fallbacks, 100% rule-check pass, 3.4 s per brief, $0.27 for the models plus about $3.90 of one-off Places searches (now cached). Reading all 20 pack briefs found problems the rule checks can't see, so the pack was held back:
1. **Weather swaps landed on other plans** (Monkey Forest moved into lunch). Now code picks the time: free, dry, not too hot, long enough, venue open. If nothing fits, there is no swap and the model suggests a precaution instead.
2. **Departure day**: free time now ends 3 hours before an international flight; with no transfer planned, the brief says when to leave for the airport.
3. **Arrival day**: no suggestions before hotel check-in.
4. **Unknown opening hours**: only any-time places (parks, beaches, temples, landmarks) may still be suggested; a "night market" was being suggested at 07:00. Listicle articles from web search are no longer candidates.
5. **Rain**: landmarks and places with "beach", "viewpoint" etc. in the name count as outdoor. The weather line is now written in code, so rain on a free day is always stated and the format is always "26-29°C, rain likely 13:00-17:00".
6. **Dropped checks**: code lists what the brief must say (late arrivals, closing times, pace, steep paths with children, Jain meals, timed entries, airport). A heads-up counts only if it names the item and the topic; anything missing is added in code's own words.
7. **Jain meals**: every meal not marked Jain gets "ask for dishes without onion, garlic or root vegetables". Plant-based is not enough.
8. **Polish**: surprises aren't repeated in heads-ups or the tip; the tip can't repeat the swap or name a place outside the plan; ALL-CAPS names tidied; free days say "Nothing to pay for in today's plan".
Prompts: summariser-v6, composer-v3. Tests: 116 passing. Rebuilding the contexts needs no new Places searches (all cached).

## 26 Oct (early): Second review, small fixes
The v2 pack fixed the swaps, airport and check-in buffers, the 07:00 night market and the dropped checks. Remaining: the code sometimes added a line the model had already covered in other words ("Jain-friendly", "expect delays"); beaches and bays got "check opening hours"; "Ubud" (a town) was suggested as a temple; names like "Playa Pacha | Pool and Private Beach Club| FIVE LUXE JBR". Fixed: any Jain line covers the Jain check, "delay" counts as late, no hours check for beaches and parks, towns excluded, names cleaned, Balinese temples count as outdoor in rain. Known and left for the judge: a suggestion at 19:00 on a day the brief also suggests moving dinner to 19:00. Tests: 118.
Also found in the v3 run: a free day (td.21) got no suggestions, because a 07:00-23:30 window was checked only at 07:00, when almost everything is shut. Candidates are now checked every 30 minutes for the first open time, and the suggestion shows that time ("17:00-23:30"). Tests: 120.

## 27 Oct: Agent 4, the judge
- **Two layers.** Rule checks in code first (no model, no cost), then the judge model scores each rubric criterion 0-100 with a one-sentence reason and lists concrete issues. The scale's anchors (100 nothing to fix, 80 small slip, 60 one real problem, 40 several or misleading, 20 wrong) match the 1-5 scale the human reviewers use, so the two can be compared directly.
- **Code decides, not the model.** Weighted score from the rubric's weights; badge from the rubric's thresholds (green 85, amber 60); decision: pass if green and no rule failure; otherwise fix, once (`max_fix_attempts: 1`), by sending the judge's reasons back to the Composer; the fix is judged again and kept only if it is not worse; still red or breaking a rule → the safe version.
- **Never blocks.** If the judge model fails or times out, the rule checks decide alone and the brief goes out without a badge, flagged "judge unavailable".
- **The judge must score every criterion exactly once**; anything else counts as a judge failure, not a judgement.
- **Separate service and separate runs**: `POST /judge` scores any brief for a trip day; `python -m mb.evals.judge_run <run>` judges stored briefs without rewriting them, with `--model` (compare judges) and `--repeat` (consistency).
- Runs now record each brief's verdict, score, badge, criterion scores and the judge's issues. Tests: 133.

## 27 Oct: First judged run, and judge-v2
judge-v1 inside the pipeline (run `judged-v1`): 22 days, average 74.7, 2 green / 19 amber / 1 red, 14 fixes, 1 fallback, 12.4 s per brief, $0.31. Reading every verdict showed the judge was mostly wrong in two ways, not the briefs:
1. **It disputed facts computed in code.** "No rain expected" was marked inaccurate because one activity had a 5% chance of rain, on almost every brief.
2. **It marked the brief down for problems in the plan.** Monkey Forest closing at 17:00, a 14-minute late arrival, a late dinner: the brief reported each one correctly, but the judge scored feasibility 40-60 and asked to "adjust the timeline", which the brief can't do. The Composer couldn't fix what wasn't wrong, so most fixes were wasted calls, and td.18 fell back to the safe version for nothing.
It also asked for a timeline on a free day.
**judge-v2** gives the judge the code's facts as facts (`facts_from_code`), says the plan is fixed (judge how the brief reports problems, not the problems), and says a free day has no timeline. judge-v1 results are kept as the record of the first version. The real test of the judge is agreement with the team's blind scores.
Also: an idea's start time is rounded to the quarter hour ("10:02" became "10:15").

## 27 Oct: judge-v2 results, and the comparison tools
judge-v2 inside the pipeline (run `judged-v2`): average 88.2 (was 74.7), 13 green / 9 amber / 0 red, 8 fixes (was 14), 0 fallbacks (was 1), 9.2 s per brief, $0.28. On the 22 pack briefs (`judge_run baseline-v5`): average 87.8, $0.0085 for all 22.
Still seen with gpt-4o-mini: a few "rain up to 5%" complaints and Monkey Forest's 17:00 closing still blamed on the brief, and one irrelevant issue (flight and hotel cost). I stopped tuning the prompt here: further changes would fit the judge to my own opinion. The next evidence comes from other judge models on the same briefs, and from the team's blind scores.
New tools:
- `runner --set role=model`: swap one agent's model for one run (summariser, surprise_finder, composer, judge). Comparisons change one role at a time, with the judge fixed, on the same stored contexts.
- Records now keep `first_score`, the judge's score before any fix: the fair comparison metric, because the fix loop would otherwise hide a weaker model.
- `scorecard`: every judged run and every judge side by side, written to `data/runs/scorecard.html`. For judges: average score, spread between repeats of the same brief, how often the badge stays the same, cost.
Tests: 135.

## 27 Oct: The first comparison runs were silently invalid
Nine comparison runs and three judge runs finished with normal-looking numbers. Reading the events showed most of them never used the model being tested:
- **Claude Sonnet 5.5 and Haiku 5.5 reject `temperature=0`.** Every Claude call failed. The Composer fell back to the code merge, the Summariser to the safe version (22 of 22), and Haiku produced 0 of 66 judgements.
- **Gemini's free tier allows 20 requests per model per day.** After 20, every call failed with a 17-hour wait; Gemini flash-lite judged 15 of 66.
- Only the three GPT-4.1 mini runs and the gpt-4o-mini judge were real.
The lesson: **graceful degradation can quietly corrupt an evaluation.** The pipeline did exactly what it should for a traveller (never block), and that hid the failures from the numbers.
Fixes:
- `models.yaml` marks models that don't accept a temperature (`temperature: false`); the LLM wrapper leaves it out for them.
- **Preflight:** before any run or judge run, one tiny call to every model it uses. If one can't answer, the run doesn't start.
- Runs record `agent_failures`; the scorecard marks a run or judge **valid: NO** if anything fell back or failed.
The invalid runs are kept and shown as invalid, not deleted. Tests: 138.

## 27 Oct: Valid comparisons, and three judges that disagree
Preflight stopped all three Sonnet runs before they started: Sonnet 5.5 also rejects forced tool calls, which is how Instructor gets structured output by default. Sonnet now uses Instructor's JSON mode (`structured: json` in models.yaml).
Valid agent results (same 22 contexts, same judge gpt-4o-mini, first score before any fix; GPT-4o baseline 88.2 in judged-v2):
| role | GPT-4.1 mini | Claude Haiku 5.5 |
|---|---|---|
| summariser | 86.1 | 86.3 |
| surprise finder | 87.7 | 86.3 |
| composer | 85.9 | 86.5, but **14 of 22 fell back**: its notification body ran over 90 characters (100-115), the fix didn't shorten it, so the rule check sent the safe version |
The rule checks and fallback did their job: no traveller would have seen an over-long notification.
Judges on the same 22 pack briefs, 3 repeats each:
| judge | avg | spread between repeats | same badge | red |
|---|---|---|---|---|
| gpt-4o-mini | 87.7 | 1.2 | 100% | 0 |
| gpt-4.1-mini | 99.0 | 1.6 | 100% | 0 |
| claude-haiku-5-5 | 66.1 | 3.7 | 77% | 17 |
They disagree by 33 points on the same briefs. gpt-4.1-mini passes almost everything; Haiku is strict, less consistent, and partly right (it caught a tip that invented a "Coffee Museum" and heads-ups that state a time without saying what to do) and partly wrong (it called the code-computed spend "unsupported"). Both OpenAI judges score briefs written by OpenAI models higher, which may be self-preference or leniency; the team's blind scores will tell which judge is closer to people.
Also: spend and timeline added to the judge's `facts_from_code`; "cash only" added to what the brief must report (Haiku's Composer dropped it once). Tests: 140.


## 27 Oct: Comparison results, all valid
Same 22 contexts, same judge (gpt-4o-mini, judge-v2), first score before any fix. GPT-4o baseline (`cmp-baseline-gpt4o`): **88.3**, 1.28 cents per brief, 9.9 s.
| role | GPT-4.1 mini | Claude Haiku 5.5 | Claude Sonnet 5.5 |
|---|---|---|---|
| summariser | 86.1 | 86.3 | 86.1 (17.2 s, 2.5 c) |
| surprise finder | 87.7 | 86.3 | 87.0 |
| composer | 85.9 | 86.5, 14/22 fell back (notification too long) | 88.1 (16.7 s, 2.2 c) |
Every valid result is within 2.4 points of GPT-4o, and the judge's own score moves about 2 points between repeats of the same brief, so **no model is clearly better or worse than GPT-4o on this set**, except Haiku as Composer, which fails the format rule. Gemini runs stay marked invalid (free-tier quota).
Judges re-run with the spend/timeline facts: gpt-4o-mini 87.8 (spread 2.1, 91% same badge); gpt-4.1-mini 98.5 (passes nearly everything); Haiku 66.7 (spread 3.7, 91% same badge, 5 red). Haiku's issues include real ones the OpenAI judges missed (an invented "Coffee Museum" tip; heads-ups that give a time but no action). Which judge to trust is decided by agreement with the team's blind scores, not by which number looks best.

## 28 Oct: Batch runner, outbox in Postgres, self-preference test
- **Batch runner** (`python -m mb.batch`): run on a schedule; each run finds every trip day whose local send time falls in the next `--window` minutes and prepares its brief ahead of time. Local send time is 07:30, or an hour before an early first activity, **in the time zone where the traveller wakes up**: home on an arrival morning that starts with the flight, the destination otherwise. Example: Bali day 4 starts at 07:00, so the brief goes at 06:00 WITA = **22:00 UTC the day before**. `--plan` lists who is due at no cost. Briefs already in the outbox are skipped, so runs can repeat or overlap safely. `wake_timezone()` was pulled out of the context builder so both use the same rule.
- **Outbox**: one brief per trip per date, with its time zone, local and UTC send times, verdict, score and badge. A file store by default; **Postgres** when `DATABASE_URL` is set. `docker compose` now starts Postgres 16 (password from `.env`, data in a named volume). `GET /outbox/{date}` lists what's ready. Tested against a real Postgres.
- Sending the notification, and when it may interrupt the traveller, belong to the app: covered in the design note.
- **Self-preference test** (`python -m mb.evals.self_preference`): the same 22 days written entirely by one family (GPT-4o) and entirely by another (Claude Sonnet), each judged by all three judges. Compares each judge's own-family preference with the other judges'.
Tests: 145 (+1 that runs when a Postgres is available).
Two Docker problems while checking the batch runner in compose:
- **A failed build silently ran old code.** pip timed out on a slow connection, the image wasn't rebuilt, and `docker compose run` started the image from 23 Oct, which had no batch runner. The Dockerfile now gives pip a 2-minute timeout, 10 retries and a download cache kept between builds; and builds are run as their own step (`docker compose build`) before `up`.
- **The API crashed on its first start**: it and a batch run both created the outbox table at the same moment, and `CREATE TABLE IF NOT EXISTS` isn't safe against that race in Postgres. Table creation now takes a Postgres advisory lock. Tested with six stores starting at once.
In compose, the batch prepared the five Dubai briefs for 07:30 local (03:30 UTC), all green, into Postgres; the second run skipped all five.

## 29 Oct: Sourabh's reply, and what changed
He agreed the structure (rule checks in code, never blocking on a judge failure, the pass-bar shape) and asked for three changes and some numbers.
- **A serious error fails the brief whatever its score.** judge-v3 lists `serious` errors separately: a wrong time, place, price or opening hour; anything invented; a diet mistake; unsafe advice (a clash with a booking, rain or heat with no warning, a steep path with children, missing a flight); dropping a check code found. Any one is treated like a rule failure: fix once, and if it survives, the plan-only version. A fix that adds a serious error is thrown away.
- **A fixed brief is checked again before it's sent**: it already was (rule checks and the judge run again on the fix); now tested explicitly for the serious case too.
- **85 and 60 are placeholders**, to be set once the judge is chosen. They were already in the rubric file; the agreement script now also reports which cut-offs would agree best with their reviewer.
- **Their reviewer** (a stronger Claude model, different from those tested) answers send as is / fix first / don't send, plus wrong or unsafe. Each verdict now carries a `recommendation` in the same three words, so agreement is a direct comparison. `python -m mb.evals.agreement <run> <reviewer file>` gives agreement, linear-weighted kappa, unsafe briefs caught and false alarms per judge.
- **What they asked for**: `python -m mb.evals.review_export baseline-v5` writes the 20 briefs (HTML and JSON, no model names), every judge's verdict by code (`judge_scores.xlsx`), and `cost_and_time.md` (cost per brief in rupees, time per brief, monthly at 50 and 100 travellers a day).
- **Every brief now carries `send_at_utc`** next to the local time and time zone: their backend sends the notification, our service returns the brief and when to send it.
Tests: 153 (+2 with a Postgres).
First judge-v3 run on the 20 pack briefs: the new serious-error list over-fired. gpt-4o-mini marked 39 of 66 judgements serious (mostly the old "5% rain" complaint and "doesn't mention unpublished hours"), Claude Haiku 49 of 66, gpt-4.1-mini none. Two causes:
1. **The judge couldn't see what we had verified about each idea**: it got only candidate names, so "highly rated, 32 min away" looked invented. Judge and reviewer now both get each candidate's rating, types, travel time and slot. (An input bug, not prompt tuning.)
2. **"Serious" was too loose.** It must now quote the wrong part of the brief and the input it contradicts; anything in facts_from_code, unpublished-hours advice and differently worded checks are never serious; "if unsure, it's an issue, not serious".
Real issues the strict judge found and kept: a tip inventing a "Coffee Museum", a tip suggesting snorkelling in the rain, an idea starting at 14:04 (fixed after these briefs were made).
Gemini flash-lite (15 of 66 judged, free-tier quota) is left out of the export: a judge with failed judgements isn't comparable.

## 29 Oct (evening): finishing the build

- Local tips are checked in code: a tip naming a place not in the plan, the candidates or the
  region, or using a risky word, or about an item with a weather clash, is replaced by a safe tip.
- Closing-time heads-ups now always carry an action ("Leave X by HH:MM").
- Kill switch (`briefs_enabled` / `MB_KILL_SWITCH`), web preview (`/preview/{date}/{trip_id}`),
  a second rubric (`morning_brief_launch.v1`) and stand-in reviewers (`mb.evals.proxy_review`)
  for when the Travezy reviewer verdicts are not yet available.
- Prices re-checked on 29 Oct 2026.
- Docs: setup guide, design note, architecture diagram, `.env.example`.
- Tests: 161 passed, 2 skipped.

## 29 Oct (late): fixes from the live test and the stand-in review

- Live `POST /brief` for dubai_up01 fell back: gpt-4o-mini flagged as serious a heads-up telling
  the traveller to check hours the souks don't publish (the prompt lists this as never serious).
  Code now confirms every serious error (`confirm_serious`): it must quote the brief, the quote
  must be in the brief, and it must not be about a computed fact or on the never-serious list.
  Unconfirmed flags are kept as issues and recorded as `serious_dropped`.
- A fallback brief no longer carries the rejected draft's score and badge; the draft's score is a flag.
- The code's late-finish line read like raw check text in B02; it now reads as a heads-up.
- `agreement --between` measures how much two reviewers agree: the ceiling for any judge.
- Tests: 165 passed, 2 skipped.

## 30 Oct (00:55): live exchange rates

- The spend line's home-currency figure used a fixed rate from config/fx.yaml (88). It now uses
  today's live rate (mb/sources/fx.py), fetched once a day and cached; a rate up to 2 days old if
  the source is down; otherwise no home figure at all. Tests use fixed rates.
- Cost report rate updated to 95.22 (30 Oct 2026).
- Tests: 169 passed, 2 skipped.

## 30 Oct (01:20): checks before sending the review pack

- Review export: judgements made before `confirm_serious` existed are re-confirmed with the same rule
  (no model call), so judge_scores.xlsx follows the code that runs today. On baseline-v5,
  gpt-4o-mini's briefs with a serious flag went from 39/66 to 4/66: 19 flags quoted nothing from the
  brief and 17 were on the never-serious list. The three "omissions" among them (JBR heat, Jain line,
  Monkey Forest closing) were checked by hand: all three were in the briefs. Haiku: 30 -> 30.
  Dropped flags are listed in a new column, "flags code did not confirm".
- Tips: a tip may not make a safety claim no source supports ("safe", "perfect for children",
  "calm waters"). From B18 in the stand-in review.
- Free windows end 30 minutes before a pickup or transfer at the end of the day, not at it (B18:
  an idea ran 14:04-15:00 with the airport taxi at 15:00).
- Cost report: the rate's check date and Google's free allowances are computed from config, not typed.
- Stand-in agreement on baseline-v5 (20 briefs): the two stand-in reviewers agree with each other on
  56% of decisions (weighted kappa 0.07, close to chance). Best judge vs Opus: gpt-4o-mini, 55%,
  kappa 0.21; none of the three judges caught Opus's 4 unsafe briefs (Haiku caught 1). Small sample:
  the Travezy reviewer's verdicts decide.
- Tests: 171 passed, 2 skipped.
- Review pack inputs: briefs.json re-derived the code's checks with today's code, so 4 late-finish
  lines (B02, B11, B14, B19) showed tonight's new wording instead of what the writers saw. Runs now
  save each brief's exact input (`inputs/<day>.json`) and the export uses it. baseline-v5's inputs were
  rebuilt with the code that wrote it (commit bc6fbb6) and saved; briefs.html, from the stored
  contexts, was already exact.

## 30 Oct: clean-clone test

- Fresh clone from GitHub, one command: built, fetched its own data, wrote a real brief (bali_up04,
  11 Dec, rain): leave-by line, rain heads-up, live-rate spend, tip from the plan, 2 checked ideas.
- First brief took 90.0 s: LiteLLM tried to download its price list from GitHub (3 timeouts, ~50 s).
  Now uses its built-in list (mb/__init__.py), which covers every model we use. Second run: 37.8 s.
- Known judge limit: gpt-4o-mini reads "Leave X by 17:00: it closes before your planned 17:30
  finish" as "open until 17:30" and lowers accuracy (64 -> 71 after the fix, amber). The brief is
  correct. Not changed while the team reviews against judge-v3; candidate fix for judge-v4: closing
  times as explicit facts.

## 31 Oct: baseline-v6 and fixes from its stand-in review

- baseline-v6 (summariser-v7, composer-v4, judge-v3, stored contexts): 22/22, 0 failures, 0 fallbacks,
  100% rules, first score 86.5, 10.2 s and $0.0131 per brief. gpt-4o-mini on the same 20 pack briefs,
  3 repeats: 84.6 (v5) -> 85.0 (v6), badge consistency 86% -> 95%. Opus stand-in: unsafe 4 -> 3;
  B17 ("children swim safely" tip) and B20 are gone.
- Opus's v6 findings, fixed in code:
  - Outdoor ideas ignored heat and were offered across rain (B18 water park at 38°C; B03 beach
    10:00-23:30 through rain 13:00-17:00). Outdoor candidates now need a dry, not-too-hot visit and are
    shown only for the good hours (`_good_weather_slot`).
  - "later than your usual dinner" contains "late", so the long-day line counted as covered (B11).
    Topic words fixed.
  - A planned airport taxi meant no departure line at all (B18). Now: "Airport transfer (Taxi) at 15:00
    for your 18:00 flight: be packed and ready by 14:30".
  - Vegetarian meals weren't checked in code, only Jain (B04, B06, B11). Now every planned meal not
    marked vegetarian gets one check line.
- Not fixed (known limits): idea descriptions can still overstate a place ("beach club" for a
  restaurant, "sunset views"); gpt-4o-mini still docks "no rain expected" at 5% and misreads the
  leave-by line. Judge unchanged while the team reviews against judge-v3.
- Tests: 175 passed, 2 skipped.

## 31 Oct: baseline-v7 (contexts rebuilt) and the fixes from its review

- baseline-v7: 22/22, 0 failures, 0 fallbacks, 100% rules, 8.3 s and $0.0114 per brief. First score
  83.5 (v6 86.5): the brief now carries more code lines (vegetarian checks, transfer line) and the
  judge's known misreadings remain; gpt-4o-mini vs Opus agreement 70%, kappa 0.40 (v6 -0.03).
- Opus stand-in flagged 7 unsafe (v6: 3). Read one by one, 5 were real, and the worst were the models
  writing their own times in our "Leave X by HH:MM" style, wrongly: "Leave Kelingking by 11:16"
  (should be 11:46), "Depart Penida Colada by 13:01", tips "Leave Al Fahidi by 11:00", "Leave the
  souks by 14:30". Fixes, all in code:
  - Time check: a heads-up or tip may only state a time from the plan, the free windows, the send time
    or a line code wrote (`known_times`, `unknown_times`). A line with any other time is dropped (the
    code's own line is added back if needed); a tip with one is replaced.
  - Idea slots end 15 min before they must leave, with travel to the next stop, or to the hotel when
    the next item is a pickup (B18 aquarium until 14:30 with the taxi at 15:00; B04 16:26 + 32 min for
    a booked 17:00 entry). One idea per free window.
  - Idea descriptions and tips may not claim what no source confirms ("rooftop dining", "beach club",
    "close to hotel", "best views at sunset", "thrilling"); an idea's description falls back to facts:
    "A cafe, rated 4.4, 9 min away, open in this slot."
  - The notification's idea count is set by code ("plus 0 ideas for you", B01).
  - A dinner line with the planned time covers the pace check (no duplicate, B11).
- Tests: 181 passed, 2 skipped.

## 31 Oct: baseline-v8 (final run before the walkthrough; code frozen after this)

- baseline-v8 (contexts rebuilt): 22/22, 0 agent failures, 100% rules, 7.9 s and $0.0115 per brief,
  first score 84.5, final 85.7. One fallback, correctly: after the fix step the model wrote "Travel
  time: Kelingking Beach Viewpoint to Penida Colada is 30 min" (it is 44; 30 is the gap). The judge
  flagged it as serious, code confirmed the quote, and the traveller got the safe version, which says
  "about 44 min of travel ... about 14 min late".
- Opus stand-in: 15 send, 5 fix first (v7: 9 send); unsafe 3 (v7: 7). No invented times, no idea
  running into the next stop, no "rooftop/beach club" claims. gpt-4o-mini vs Opus: 75% agreement,
  kappa 0.44, its best (v5 0.21, v6 -0.03, v7 0.40); it still caught 0 of the 3 unsafe briefs.
- Two of the 3 unsafe flags (B11, B14) had one cause: the plan says Tegallalang has "uneven paths",
  and the access check only looked for steep/stairs/steps. Fixed: "Tegallalang Rice Terrace: uneven
  paths, keep the children (6 and 10) close and wear good shoes" / "... take it slowly and stay near
  the viewpoint". The third (B18) is an idea at lunchtime for a family that rests after lunch: meal and
  rest times are not modelled yet (known limit).
- Known limits, unchanged: gpt-4o-mini docks "no rain expected" at a 5% chance and misreads the
  leave-by line; a minutes figure written by a model is not code-checked (times are).
- Tests: 182 passed, 2 skipped. Code frozen for the walkthrough except for a wrong time or a safety risk.

## 2 Nov: run results queryable through Brief 1

- The brief asks for every run's scores, verdicts, response time and tokens in a table the Brief 1
  insight tool can query. The data was in each run's `records.jsonl`, but not in one table and not
  linked to Brief 1. Added `mb/evals/export_runs.py`: one row per brief per run (528 rows from 24
  runs) with the model under test, its role, the day kind (arrival, sightseeing, departure,
  free_day), first and final score, verdict, badge, failed, response time, tokens and cost.
- Failed = safe version sent, an agent failed, or a rule check failed. Older records have no
  `agent_failures` field, so it is read from the events, the same rule the runner uses.
- The three Gemini runs are marked invalid (free-tier quota) and left out of the view Brief 1 reads.
- In Brief 1: `analytics.brief_runs` (loaded by `scripts/load_morning_brief.py`, read-only user),
  nine metrics and eight dimensions in the semantic layer, golden questions and tests.
- "Which model failed most often on arrival days?" -> Claude Haiku 5.5 (as Composer): 3 of 9 arrival
  briefs, 33.3%; GPT-4o 0 of 33, Sonnet 0 of 12, GPT-4.1 mini 0 of 9.
- Tests: 186 passed, 2 skipped.

## 3–4 Nov: actions from the walkthrough

All the points from Sourabh's 3 Nov email. The reply goes through them one by one.

**Built:**
- **Login:** an API key on every endpoint except `/health`. With no key set, the service refuses every request (it fails closed).
- **Postgres:** the service's own schema with a migration script (`python -m mb.db`). It holds the outbox, the paid-call ledger, budget alerts, a shared cache and the places table.
- **Budget:** a monthly budget with alerts at 50% and 80% (each fires once a month, even with several copies running) and a hard stop at 100%.
- **Places table:** a stop is found once with the free IDs-only search, then read from Place Details. Hours are refreshed every 3 days, and searches for ideas every 30 days.
- **Prices:** Google's India pricing.
- **Limits:** every timeout and retry in `config/limits.yaml`. Weather comes from WeatherAPI.com, switched in config.
- **Models:** end-of-life dates with a warning 90 days ahead. Model settings such as `reasoning_effort` are set in config only.
- **Badge:** covers only the AI-written parts (`Confidence.applies_to`).
- **Quality (blind review):**
  - Titles name the day's key moments.
  - No ideas in a family's rest break, and no food idea straight after a meal.
  - A diet clash offers a checked alternative.
  - The judge can't call a verified place invented.
- **Ops:** old prompts archived, no default database password, retention clean-up (`python -m mb.db cleanup`), and `--concurrency` for the batch.
- **Docs:** `production_notes.md`, `loader_spec.md`, `cost_sheet.md`, and Mac and Linux setup.

**Replacement models for the January 2027 retirements** (one role swapped at a time, judge gpt-4o-mini, same 22 days):

| Role tested | Model | First score | Agent failures | Fallbacks | Time per brief | Cost per brief |
|---|---|---|---|---|---|---|
| All (baseline-v9) | GPT-4o | 82.4 | 0 | 0 | 9.4 s | $0.0139 |
| Summariser | gpt-5-mini, minimal reasoning | 83.5 | 0 | 3 | 13.2 s | $0.0095 |
| Surprise finder | gpt-5-mini | 84.5 | 0 | 2 | 16.3 s | $0.0126 |
| Composer | gpt-5-mini, minimal reasoning | 81.8 | 0 | 3 | 14.4 s | $0.0092 |
| Judge (re-judging v9) | gpt-5-mini | avg 88.8 | 0 | n/a | n/a | $0.0015 a judgement |

- **At its default reasoning, gpt-5-mini timed out:** 10 of 22 briefs as Summariser, and 14 agent failures as Composer, because it kept thinking past the 30 s limit. Setting `reasoning_effort: minimal` in `config/models.yaml` fixed it with no code change.
- **Scores are within the judge's noise (about 2 points) in every role.** gpt-5-mini cuts model cost by about 30% as Summariser or Composer. It's 4 to 7 s slower, which doesn't matter for briefs prepared ahead.
- **Watch point:** 2 to 3 fallbacks in each gpt-5-mini run, against 0 for GPT-4o on the same days. Read those briefs before switching.
- baseline-v9 (all the fixes, GPT-4o): 22/22, 0 fallbacks, every rule passed, first score 82.4 (v8: 84.5, within noise), 11 green and 11 amber. Review pack in `data/runs/baseline-v9/review`.
- Tests: 201 passed (plus the Postgres tests, run against a test database).

## 6 Nov: Travezy's blind review of the rerun, and what came out of it

**Their verdicts on the v9 pack:** send 10 (up from 4 last time), fix first 9, don't send 1 (B18).

**Why the v9 pack missed fixes (their point 2):** reruns reuse each test day's stored facts
(`data/contexts`, built 25 Oct), so every model sees identical inputs. But the idea filters (airport taxi,
meal and rest) run while those facts are built, so they never ran on v9. The pack is now regenerated with
`--rebuild` from current code (baseline-v12, commit `4a240bd` plus the fix below).

**Built:**
- **Backend:**
  - `GET /briefs/{brief_id}` returns a stored brief.
  - `POST /briefs/{brief_id}/sent` marks it sent.
  - `GET /outbox?due_from=&due_to=` queries by UTC send time.
  - `brief_id` and `sent_at` columns (migration 002).
- **Batch:** the loader is asked only for trips with a day in the window. Stored test weather is off unless `--scenarios`.
- **Production settings:** with `MB_ENV=production` the key is header-only. `/health` returns only `ok`; spend moved to `/status`.
- **Quality:**
  - Walking limit: cautions on long-walk stops, and only seated ideas at 3 km or less, with a "cafes and museums" search so the day isn't empty.
  - Safety cautions in `config/safety.yaml`: monkeys for everyone (children named), dune bashing and hikes for older travellers and children.
  - Jain travellers get a vegetarian place with "ask for Jain preparation"; a model's bare "ask for options" line is replaced when code has an alternative.
  - Weather clashes with no dry slot get an indoor swap.
  - The rest break is kept free with no planned lunch.
- **Judge:** a "doesn't mention X" flag is a fix, not serious (code enforces must-say facts), and the timeline is treated as code-written.
- **Evaluation:** the agreement script finds the scores on any sheet; their file opened on "How to score", so it had read 0 briefs.
- **Docs and cost:**
  - The cost report uses the Postgres ledger, India prices, the free allowance and a worst-case column.
  - Loader spec matched to their format, with each field marked "exists today" or "new".
  - Prompts summariser-v8 and composer-v5: the title is set by code.
  - Docker mounts removed, design-note list updated, connection pool added to the scaling plan.

**Judges against Travezy's 20 verdicts (v9 pack):**

| Judge | Agreement | Weighted kappa | Unsafe caught |
|---|---|---|---|
| gpt-4o-mini (current) | 45% | 0.00 | 0 of 3 |
| gpt-5-mini (replacement) | 55% | 0.09 | 0 of 3 |

Recommendation: gpt-5-mini. Neither catches unsafe briefs, which is why safety stays in code.

**gpt-5-mini fallbacks explained:** of 8 fallbacks across its three runs, 6 were the gpt-4o-mini judge
flagging "doesn't mention X" for facts code already guarantees, or quoting the code-written timeline. 2 were
real: an unsupported "swimming not advised" claim, and a missed timed entry. The judge rule above removes the 6.

**Runs (GPT-4o, judge gpt-4o-mini, facts rebuilt):**

| Run | Fallbacks | First score | Green / amber | Days with no idea | Per brief |
|---|---|---|---|---|---|
| v10 | 1 | 84.5 | 13 / 8 | 8 | $0.0134 |
| v11 | 0 | 83.9 | 14 / 8 | 12 | $0.0130 |
| v12 | 0 | 85.2 | 13 / 9 | 9 | $0.0133 |

Checked in v12 against their notes:
- B18: no aquarium before the taxi.
- B19: no second lunch.
- B07: walking and dune-bashing cautions.
- B14: monkey caution naming the children, plus an indoor swap.
- B04 and B05: a checked vegetarian alternative.
- B12: an indoor swap.
- B02: still "ask for Jain options". There's no vegetarian place among that day's checked ideas, and code never invents one.

B19 had the monkey caution twice (the model's and code's); fixed after v12.

### Testing before the reply (6 Nov)

- **Brief audit (new, `python -m mb.evals.audit`):** every brief is checked against its facts:
  - Limits, and a title that isn't generic.
  - Every idea is a checked candidate inside its slot, with no clash with a planned stop, a rest break, a meal, the weather or the walking limit.
  - No unknown times or unsupported claims in the tip, every must-say fact covered, no duplicates, a swap when one exists.
- **What the audit found on v13, and the fixes:**
  - The tip check read "31°C" as a place called "C", so a model's valid heat tip could be dropped.
  - A model's vague dinner line sat next to code's.
- **v14 audit result:** 22 of 22 briefs clean.
- **Full suite against a real Postgres:** found `PostgresStore(url)` ignoring its address (it always used `DATABASE_URL`); fixed.
- **New tests:** six processes migrating at once, and migration 002 upgrading a database that already holds briefs.
- **Suite result:** 234 passed, three runs in a row, nothing skipped.
- **End-to-end (`scripts/smoke_check.py`) against the running Docker service:** 20 of 20, covering:
  - Health and status.
  - Key refused when missing or wrong.
  - Outbox by send time, fetch by brief_id, mark sent (safe twice), a sent brief leaves the due list.
  - Preview by header only.
  - POST /brief, and a date not on the trip refused.
- **baseline-v14 (the pack sent):** 22/22, 0 fallbacks, 0 agent failures, every rule passed, 13 green and 9 amber, first score 82.9 (v12 85.2, v13 84.4: within the judge's noise), $0.0131 per brief. Same codes as the pack Travezy reviewed.

## 10 Nov: handover fixes from Travezy's v14 scoring sheet

Brief 6 is closed. Sourabh sent five handover points with the scoring sheet for the v14 pack (12 send, 7 fix first, 1 don't send). All five are fixed in code, plus three smaller patterns from the sheet's comments.

| Point | Brief | Fix |
|---|---|---|
| Closing time stated wrongly | B19 (don't send): "leave by 16:00" against a 17:00 close | The closing line is only ever code's, from the flag; any model line about closing or leaving that place is dropped |
| Too many heads-ups, repeats | B11: 9 lines, the walking caution twice | Must-say lines first and never dropped; one walking line for all stops; model extras fill up to 4, near-copies removed (B11 now 7, all must-say) |
| Places suggested twice | B04-B06, B12, B14 | An idea can't repeat the swap or the diet alternative, or be a second food place in the same window; the indoor swap must be open during the clash |
| Places already in the plan | B14: "or try Zest" on a Zest Ubud day | Diet alternatives and swaps skip anything already planned (name match either way) |
| Wording | B12, B08 | Swap names the forecast ("Up to 38°C at JBR Beach: swap it for..."); beach and promenade walks say "turn back when you've had enough", not "taxi" |
| Camp dinner unchecked | B07, B10 | A meal inside an activity (dinner, BBQ, buffet) gets the diet check |
| Late finish, no advice | B02, B14 | The long-day line says what to do ("ask the driver to finish by 20:00") |

Tests: 233 passed, 8 skipped (5 new). Commit 88a463d.

**baseline-v15** (same contexts, no rebuild): 22/22, 0 fallbacks, rules 100%, audit 22/22 clean, 17 pass / 5 fix, 13 green / 9 amber, average score 85.2 (v14: 82.9), $0.30 for the run.

**Judges against Travezy's v14 verdicts (20 briefs):**

| Judge | Agreement | Weighted kappa | Unsafe caught | 6 Nov (v9) |
|---|---|---|---|---|
| gpt-5-mini | 65% | 0.255 | 0/1 | 55%, 0.09 |
| gpt-4o-mini | 55% | 0.151 | 0/1 | 45%, 0.00 |

Both judges wrote B19's wrong closing time in their notes but didn't mark it serious, so the brief wasn't blocked; the fix above removes the cause in code. gpt-4o-mini's notes are mostly noise ("no rain expected" against a 5% chance on 8 briefs). gpt-5-mini stays the recommendation, with cutoffs green 90 / amber 85 on this sheet (kappa 0.365): 20 briefs is too few to set them for good.

**Left open:**
- B18 (38°C beach with children) and B12: no indoor place nearby is open during the heat, so there is no swap; the heads-up gives the heat advice.
- td.05: the camp-dinner diet alternative is a cafe open at breakfast time; alternatives aren't matched to the meal's time yet.
- openai/gpt-4o reaches end of life on 1 Jan 2027: the writers need a successor model chosen and re-run before then.
