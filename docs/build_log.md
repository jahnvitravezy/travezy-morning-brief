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
