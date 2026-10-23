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

## 23 Oct: Agents 2 and 3, the runner, the API and Docker
Agent 2 picks only from candidates gathered and filtered in code (open, reachable within the window including travel, not already planned, weather and children checked); its items are built from candidate data, so names, links and distances can't be invented. With no candidates the model isn't called. Agent 3 merges in code and writes only the final words; if it fails, the code-only merge is used. The runner finds agents in the registry by output, runs Agents 1 and 2 in parallel, times out and retries each step inside a 90-second budget, and degrades instead of blocking (no surprises, code merge, or the safe version). First complete brief served by the API inside Docker: about 6 s, $1.01 spent on the build so far.

## 23 Oct: First full brief reviewed
The surprise was "Future Photo Studio", a photo printing business, for a couple interested in photography. The cached searches showed why: "photography" returned studios, and "heritage" returned hotels and clothing shops. Fixed with interest-to-search mapping ("photography" -> "scenic photo spot viewpoint") and a type allowlist, both in config/candidates.yaml. Also: the Composer shortened the vegetarian heads-up and lost its reason (composer v2 keeps reasons); Windows PowerShell showed ₹ and ° garbled because the API didn't state its charset (now UTF-8 explicitly); meta.tokens was empty (now per agent). requirements.txt listed pywin32, which would break the Linux Docker build; now Windows-only.
