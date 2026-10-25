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
