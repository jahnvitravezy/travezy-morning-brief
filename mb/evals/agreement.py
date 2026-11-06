"""Agreement between our judges and the Travezy team's independent reviewer.

  python -m mb.evals.agreement baseline-v5 reviewer.xlsx      (or .csv)

The reviewer file has one row per brief: code (B01..B20), decision (send / fix first / don't send)
and wrong_or_unsafe (yes / no). For each of our judges (data/runs/<run>/judged/*.jsonl) it reports:
  agreement   share of briefs where the judge's recommendation equals the reviewer's decision
  kappa       Cohen's kappa, linear-weighted (send < fix first < don't send): agreement beyond chance;
              a near miss (fix first vs send) counts less against the judge than a far one
  unsafe      of the briefs the reviewer called wrong or unsafe, how many the judge flagged as serious
  cut-offs    the green/amber thresholds that would agree best with the reviewer, from these 20 briefs
              only (a starting point, to confirm on new briefs: 20 is a small sample)
The judge with the highest kappa is the recommended one.

  python -m mb.evals.agreement --between reviewer_a.xlsx reviewer_b.xlsx

How much two reviewers agree with each other. This is the ceiling: no judge can be expected to
agree with a reviewer more than two strong reviewers agree with each other.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from mb.evals.review_export import judge_table
from mb.evals.runner import RUNS

ORDER = ["send", "fix_first", "dont_send"]


def norm_decision(text: str) -> str:
    t = str(text).strip().lower().replace("'", "").replace("’", "").replace("-", " ").replace("_", " ")
    if t.startswith("send"):
        return "send"
    if t.startswith("fix"):
        return "fix_first"
    if t.startswith("dont") or t.startswith("do not") or t.startswith("no"):
        return "dont_send"
    raise ValueError(f"unknown decision '{text}'")


def read_reviewer(path: Path) -> dict[str, dict]:
    if path.suffix == ".csv":
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    else:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        # The scores are on whichever sheet has a "decision" column (Travezy's file opens on
        # "How to score", so the first sheet isn't it: 6 Nov).
        rows = []
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            head = [str(h).strip().lower() for h in (next(it, None) or [])]
            if "decision" in head and "code" in head:
                rows = [dict(zip(head, r)) for r in it if r and r[0]]
                break
    out = {}
    for r in rows:
        r = {str(k).strip().lower(): v for k, v in r.items()}
        if not r.get("decision"):
            continue
        unsafe = str(r.get("wrong_or_unsafe", r.get("unsafe", "no"))).strip().lower() in ("yes", "y", "true", "1")
        out[str(r["code"]).strip().upper()] = {"decision": norm_decision(r["decision"]), "unsafe": unsafe}
    return out


def weighted_kappa(a: list[str], b: list[str]) -> float | None:
    """Linear-weighted Cohen's kappa for the three ordered decisions."""
    k, n = len(ORDER), len(a)
    if n == 0:
        return None
    idx = {c: i for i, c in enumerate(ORDER)}
    w = [[abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        obs[idx[x]][idx[y]] += 1 / n
    pa = [sum(obs[i]) for i in range(k)]
    pb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    d_obs = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    d_exp = sum(w[i][j] * pa[i] * pb[j] for i in range(k) for j in range(k))
    return None if d_exp == 0 else round(1 - d_obs / d_exp, 3)


def _rec(v: dict, green: int, amber: int) -> str:
    if v["serious"]:
        return "dont_send"
    return "send" if v["score"] >= green else "fix_first" if v["score"] >= amber else "dont_send"


def compare(run_id: str, reviewer: dict[str, dict], green: int = 85, amber: int = 60) -> list[dict]:
    key = json.loads((RUNS / run_id / "pack_key.json").read_text(encoding="utf-8"))["codes"]
    rows = []
    for model, days in judge_table(run_id).items():
        pairs = [(code, days[key[code]["day_id"]]) for code in reviewer if code in key and key[code]["day_id"] in days]
        theirs = [reviewer[c]["decision"] for c, _ in pairs]
        ours = [_rec(v, green, amber) for _, v in pairs]
        unsafe = [c for c, _ in pairs if reviewer[c]["unsafe"]]
        caught = [c for c, v in pairs if reviewer[c]["unsafe"] and v["serious"]]
        best = max(((g, a, weighted_kappa([_rec(v, g, a) for _, v in pairs], theirs) or -1)
                    for g in range(70, 100, 5) for a in range(40, g, 5)), key=lambda t: t[2]) if pairs else None
        rows.append({"judge": model, "briefs": len(pairs),
                     "agreement": f"{sum(x == y for x, y in zip(ours, theirs)) / len(pairs):.0%}" if pairs else None,
                     "kappa": weighted_kappa(ours, theirs),
                     "unsafe_caught": f"{len(caught)}/{len(unsafe)}" if unsafe else "none flagged",
                     "false_alarms": sum(1 for c, v in pairs if v["serious"] and not reviewer[c]["unsafe"]),
                     "best_cutoffs": f"green {best[0]}, amber {best[1]} (kappa {best[2]})" if best else None})
    return sorted(rows, key=lambda r: -(r["kappa"] if r["kappa"] is not None else -9))


def between(a: dict[str, dict], b: dict[str, dict]) -> dict:
    """Two reviewers on the same briefs: decision agreement, kappa, and where they split."""
    codes = sorted(set(a) & set(b))
    da, db = [a[c]["decision"] for c in codes], [b[c]["decision"] for c in codes]
    return {"briefs": len(codes),
            "agreement": f"{sum(x == y for x, y in zip(da, db)) / len(codes):.0%}" if codes else None,
            "kappa": weighted_kappa(da, db),
            "unsafe_both": [c for c in codes if a[c]["unsafe"] and b[c]["unsafe"]],
            "unsafe_only_a": [c for c in codes if a[c]["unsafe"] and not b[c]["unsafe"]],
            "unsafe_only_b": [c for c in codes if b[c]["unsafe"] and not a[c]["unsafe"]],
            "decision_splits": [f"{c}: {a[c]['decision']} vs {b[c]['decision']}" for c in codes
                                if a[c]["decision"] != b[c]["decision"]]}


def main(run_id: str, path: str):
    rows = compare(run_id, read_reviewer(Path(path)))
    for r in rows:
        print(r)
    if rows:
        print(f"\nRecommended judge: {rows[0]['judge']} (highest kappa)")
    out = RUNS / run_id / f"agreement__{Path(path).stem}.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    if sys.argv[1] == "--between":
        for k, v in between(read_reviewer(Path(sys.argv[2])), read_reviewer(Path(sys.argv[3]))).items():
            print(f"{k:16} {v}")
    else:
        main(sys.argv[1], sys.argv[2])
