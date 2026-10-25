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
