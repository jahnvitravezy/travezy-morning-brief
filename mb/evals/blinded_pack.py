"""Blinded scoring pack for the human reviewers.

  python -m mb.evals.blinded_pack baseline

From a finished run it picks 20 briefs (keeping every required case covered), shuffles them
with a fixed seed and gives each a code, B01..B20. It writes:
  pack/briefs.html        each brief next to the inputs it was written from. No scores, no
                          badge, no model names: reviewers judge the brief, not the judge.
  pack/scoring_sheet.xlsx one row per code, the same five criteria the judge uses, scored 1-5.
  pack_key.json           code -> test day. Private: stays with us, used to measure agreement.
"""
from __future__ import annotations

import html
import json
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from mb.config import load_config
from mb.contracts import Context, MorningBrief
from mb.evals.runner import CONTEXTS, RUNS, context_index
from mb.evals.testset import REQUIRED_COVERAGE, load_testset

PACK_SIZE = 20
SEED = 6
SCALE = {5: "Excellent: nothing to fix", 4: "Good: a small slip a user would forgive",
         3: "OK: one real problem", 2: "Poor: several problems, or one that misleads",
         1: "Unusable: wrong or unsafe to follow"}


def choose(records: list[dict], n: int = PACK_SIZE, seed: int = SEED) -> list[dict]:
    """A seeded sample of n records that still covers every required case."""
    rng = random.Random(seed)
    for _ in range(1000):
        pick = rng.sample(records, min(n, len(records)))
        if REQUIRED_COVERAGE <= {c for r in pick for c in r["covers"]}:
            return pick
    raise ValueError("cannot cover every case with this many briefs")


# ---------- HTML ----------
def e(x) -> str:
    return html.escape(str(x)) if x not in (None, "") else "&mdash;"


def inputs_html(ctx: Context) -> str:
    p, d = ctx.preferences, ctx.day
    acts = "".join(
        f"<tr><td>{a.start:%H:%M}{'&ndash;' + format(a.end, '%H:%M') if a.end else ''}</td><td>{e(a.name)}"
        f"{' <b>(booked)</b>' if a.booked else ''}</td><td>{e(a.cost_text) if a.cost_text else 'Not stated'}</td><td>{e(a.note)}</td></tr>"
        for a in d.activities) or "<tr><td colspan=4>Free day: nothing planned</td></tr>"
    hours = ctx.weather.hours if ctx.weather else []
    wx = " &middot; ".join(f"{h.hour:%H}h {h.temp_c:.0f}&deg;C {h.rain_prob}%"
                           for h in hours if 6 <= h.hour.hour <= 22 and h.hour.hour % 2 == 0) or "&mdash;"
    flags = "".join(f"<li>{e(f.message)}</li>" for f in ctx.flags) or "<li>No problems found</li>"
    names = {a.id: a.name for a in d.activities}
    legs = "".join(f"<li>{e(names.get(l.from_id, l.from_id))} &rarr; {e(names.get(l.to_id, l.to_id))}: "
                   f"{l.minutes} min{' (estimated)' if l.source == 'estimated' else ''}</li>"
                   for l in ctx.legs) or "<li>No travel between stops today</li>"
    windows = ", ".join(f"{w.start:%H:%M}&ndash;{w.end:%H:%M}" for w in ctx.free_windows) or "none"
    kids = f", children aged {', '.join(map(str, p.party.children_ages))}" if p.party.children_ages else ""
    return f"""
<div class=inputs><h3>Inputs</h3>
<p><b>{e(ctx.region)}</b>, {d.date:%a %d %b %Y} &middot; day {d.index + 1}{' (arrival)' if d.is_arrival else ''}{' (departure)' if d.is_departure else ''}
 &middot; wakes up at {e(d.wake_location)} &middot; brief sent {ctx.send_at_local.strftime('%H:%M') if ctx.send_at_local else '&mdash;'} local</p>
<p><b>Traveller:</b> {e(p.who)} ({p.party.adults} adult{'s' if p.party.adults != 1 else ''}{kids}) &middot; <b>Diet:</b> {e(p.diet.summary)}
 &middot; <b>Pace:</b> {e(p.pace.summary)} &middot; <b>Budget:</b> {e(p.budget)} &middot; <b>Interests:</b> {e(', '.join(p.interests))}</p>
<table><tr><th>Time</th><th>Plan</th><th>Cost</th><th>Note</th></tr>{acts}</table>
<p><b>Weather</b> (hour, temp, rain chance): {wx}</p>
<p><b>Free windows:</b> {windows}</p>
<div class=cols><div><b>Travel times</b><ul>{legs}</ul></div><div><b>Checks found</b><ul>{flags}</ul></div></div>
</div>"""


def brief_html(b: MorningBrief) -> str:
    tl = "".join(f"<li><b>{e(t.time)}</b> {e(t.name)}{' (booked)' if t.booked else ''}"
                 f"{f' &middot; {t.travel_to_next_min} min to next' if t.travel_to_next_min else ''}</li>"
                 for t in b.timeline) or "<li>Free day: nothing planned</li>"
    hu = "".join(f"<li>{e(h)}</li>" for h in b.heads_ups) or "<li>Nothing to flag today</li>"
    sp = "".join(f"<li><b>{e(s.name)}</b> ({e(s.fits_slot)}{f', {s.distance_min} min away' if s.distance_min else ''}): "
                 f"{e(s.why_you)}</li>" for s in b.surprises) or "<li>None today</li>"
    w = b.weather
    return f"""
<div class=brief><h3>Brief</h3>
<div class=notif><div class=nt>{e(b.notification.title)}</div><div>{e(b.notification.body)}</div></div>
<p><b>Timeline</b></p><ul>{tl}</ul>
<p><b>Weather:</b> {e(w.summary)}{f'<br><b>Clash:</b> {e(w.clash)}' if w.clash else ''}{f'<br><b>Suggested swap:</b> {e(w.suggested_swap)}' if w.suggested_swap else ''}</p>
<p><b>Heads-ups</b></p><ul>{hu}</ul>
<p><b>Spend today:</b> {e(b.spend_estimate.local)}{f' ({e(b.spend_estimate.home)})' if b.spend_estimate.home else ''}</p>
<p><b>Ideas for you</b></p><ul>{sp}</ul>
<p><b>Local tip:</b> {e(b.local_tip)}</p>
</div>"""


CSS = """body{font:15px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;color:#222}
.card{border:1px solid #ccc;border-radius:10px;margin:28px 0;page-break-inside:avoid}
.card h2{margin:0;padding:10px 16px;background:#f3f1ec;border-radius:10px 10px 0 0}
.row{display:grid;grid-template-columns:1fr 1fr;gap:0}.row>div{padding:4px 16px 12px}
.inputs{background:#fafafa;border-right:1px solid #eee}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid #eee;padding:3px 6px;text-align:left;vertical-align:top}
.notif{background:#1f1f1f;color:#fff;border-radius:12px;padding:10px 14px;margin:6px 0}.nt{font-weight:600}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}ul{margin:4px 0;padding-left:20px}
@media(max-width:800px){.row{grid-template-columns:1fr}}"""


def pack_html(items: list[tuple[str, Context, MorningBrief]]) -> str:
    cards = "".join(f"<div class=card><h2>{code}</h2><div class=row>{inputs_html(c)}{brief_html(b)}</div></div>"
                    for code, c, b in items)
    scale = "".join(f"<li><b>{k}</b> {v}</li>" for k, v in SCALE.items())
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Morning Brief scoring pack</title>
<style>{CSS}</style></head><body><h1>Morning Brief: blinded scoring pack</h1>
<p>{len(items)} briefs. Each shows the inputs the system had (left) and the brief it wrote (right).
Score each brief in <b>scoring_sheet.xlsx</b> against its inputs only.</p><ul style='list-style:none;padding:0'>{scale}</ul>{cards}</body></html>"""


# ---------- scoring sheet ----------
def scoring_sheet(codes: list[str], criteria, path: Path) -> None:
    wb = Workbook()
    ins = wb.active
    ins.title = "How to score"
    rows = [("Morning Brief scoring sheet",), (),
            ("Score each brief 1-5 on each criterion, using only the inputs shown next to it in briefs.html.",),
            ("Then answer: would you send this brief to the traveller as it is? (yes / no)",), (),
            ("Criterion", "What to ask"), *[(c.id, c.asks) for c in criteria], (),
            ("Score", "Meaning"), *SCALE.items()]
    for r in rows:
        ins.append(list(r))
    ins["A1"].font = Font(bold=True, size=14)
    for cell in ("A6", "B6", f"A{7 + len(criteria) + 1}", f"B{7 + len(criteria) + 1}"):
        ins[cell].font = Font(bold=True)
    ins.column_dimensions["A"].width = 16
    ins.column_dimensions["B"].width = 80

    ws = wb.create_sheet("Scores")
    head = ["code", *[c.id for c in criteria], "would_send (yes/no)", "comments", "reviewer"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="444444")
        c.alignment = Alignment(horizontal="center")
    for code in codes:
        ws.append([code])
    last = len(codes) + 1
    score = DataValidation(type="whole", operator="between", formula1="1", formula2="5", showErrorMessage=True,
                           error="Whole number from 1 to 5")
    yes_no = DataValidation(type="list", formula1='"yes,no"', showErrorMessage=True)
    ws.add_data_validation(score)
    ws.add_data_validation(yes_no)
    score.add(f"B2:{chr(ord('A') + len(criteria))}{last}")
    send_col = chr(ord("B") + len(criteria))
    yes_no.add(f"{send_col}2:{send_col}{last}")
    ws.column_dimensions[chr(ord(send_col) + 1)].width = 60
    ws.freeze_panes = "B2"
    wb.save(path)


def main(run_id: str):
    cfg = load_config()
    testset = load_testset()
    rubric = next(r for r in cfg.rubrics.values() if r.use_case == testset.use_case)
    run_dir = RUNS / run_id
    records = [json.loads(l) for l in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    index = json.loads(context_index(testset).read_text(encoding="utf-8"))
    chosen = choose(records)
    items, key = [], {}
    for i, r in enumerate(chosen, 1):
        code = f"B{i:02d}"
        ctx = Context.model_validate_json((CONTEXTS / f"{index[r['day_id']]}.json").read_text(encoding="utf-8"))
        b = MorningBrief.model_validate_json((run_dir / "briefs" / f"{r['day_id']}.json").read_text(encoding="utf-8"))
        items.append((code, ctx, b))
        key[code] = {"day_id": r["day_id"], "ctx_id": ctx.ctx_id, "brief_id": b.brief_id}
    out = run_dir / "pack"
    out.mkdir(exist_ok=True)
    (out / "briefs.html").write_text(pack_html(items), encoding="utf-8")
    scoring_sheet(list(key), rubric.criteria, out / "scoring_sheet.xlsx")
    (run_dir / "pack_key.json").write_text(json.dumps({"run_id": run_id, "seed": SEED, "codes": key}, indent=1),
                                           encoding="utf-8")
    print(f"{len(key)} briefs -> {out}\nkey (private) -> {run_dir / 'pack_key.json'}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "baseline")
