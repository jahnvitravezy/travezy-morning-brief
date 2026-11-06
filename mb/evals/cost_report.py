"""Cost and time per brief, and what a month would cost, from the real ledger and run records.

  python -m mb.evals.cost_report [run]        (run: the pipeline run to price; default cmp-baseline-gpt4o)

Two kinds of cost per brief:
  models   the four agents (incl. the judge and any fix), measured per brief in the run's records
  data     Google Places, Routes and Tavily to build the day's context; paid when a lookup isn't
           cached. Measured two ways: building the 22 test-set contexts from nothing (cold), and the
           five briefs the batch runner prepared (partly cached). Weather (Open-Meteo) is free.
Monthly = briefs per day x 30 x cost per brief. A shared cache across travellers (same hotels,
same sights) cuts the data cost; three cache levels are shown because it hasn't been measured at scale.
Prices per call come from config/prices.yaml (check against the provider pages); 1 USD = app.yaml rate.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from mb.config import CONFIG_DIR
from mb.evals.runner import RUNS, ROOT
from mb.evals.testset import load_testset

LEDGER = ROOT / "data" / "usage" / "ledger.jsonl"


def _app() -> dict:
    return yaml.safe_load((CONFIG_DIR / "app.yaml").read_text(encoding="utf-8"))


def _rate() -> float:
    return float(_app()["inr_per_usd"])


def _ledger_rows(ledger: Path) -> list[dict]:
    """Every paid call: from Postgres (usage_ledger) when DATABASE_URL is set, else the local file."""
    from mb import db
    rows = []
    if db.enabled():
        rows += [{"run_id": r[0], "kind": r[1], "item": r[2], "cost_usd": float(r[3]), "units": r[4] or {},
                  "ts": r[5].timestamp()}
                 for r in db.conn().execute("SELECT run_id, kind, item, cost_usd, units, ts FROM usage_ledger").fetchall()]
    if ledger.exists():
        rows += [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def calls_per_brief(rows: list[dict], n_ctx: int) -> dict[str, float]:
    """Paid data calls per brief when building the test days' facts from nothing."""
    api = sorted((r for r in rows if r["kind"] == "api" and r["run_id"].startswith("ctx-")), key=lambda r: r.get("ts", 0))
    # Every rebuild logs under the same run_id; split them into bursts more than 30 minutes apart and
    # use the coldest one (most calls). Later rebuilds reuse stored places, which would understate cost.
    bursts, last = [], None
    for r in api:
        if last is None or r.get("ts", 0) - last > 1800:
            bursts.append(defaultdict(float))
        bursts[-1][r["item"]] += (r.get("units") or {}).get("calls", 1)
        last = r.get("ts", 0)
    cold = max(bursts, key=lambda b: sum(b.values()), default={})
    return {k: v / n_ctx for k, v in cold.items()}


def measure(run: str = "cmp-baseline-gpt4o", ledger: Path = LEDGER, runs: Path = RUNS) -> dict:
    recs = [json.loads(l) for l in (runs / run / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    lat = sorted(r["latency_ms"] / 1000 for r in recs)
    rows = _ledger_rows(ledger)
    data = defaultdict(float)
    batch_briefs = set()
    for r in rows:
        if r["kind"] == "llm":
            if r["run_id"].startswith("batch-ctx_"):
                batch_briefs.add(r["run_id"])
            continue
        if r["run_id"].startswith("ctx-"):
            data["cold"] += r["cost_usd"]
        elif r["run_id"].startswith("batch-"):
            data["batch"] += r["cost_usd"]
    n_ctx = len(load_testset().days)
    return {"run": run, "briefs": len(recs),
            "model_usd": st.mean(r["cost_usd"] for r in recs),
            "p50_s": lat[len(lat) // 2], "p95_s": lat[max(0, int(round(len(lat) * 0.95)) - 1)], "max_s": lat[-1],
            "data_cold_usd": data["cold"] / n_ctx if data["cold"] else None,
            "data_batch_usd": data["batch"] / len(batch_briefs) if batch_briefs and data["batch"] else None,
            "batch_briefs": len(batch_briefs), "calls": calls_per_brief(rows, n_ctx)}


# A planned stop is now found with the free IDs-only search plus one Place Details call, and
# "ideas near here" with one Text Search; the measured Text Search count splits about 60 / 40.
STOP_SHARE = 0.6
WARM_SHARE = 0.2        # with the places table and shared cache, about 1 in 5 lookups is new or due a refresh


def data_usd(calls: dict[str, float], prices: dict, share: float = 1.0) -> float:
    ts = calls.get("google_places_text_search", 0)
    usd = (ts * STOP_SHARE * prices["google_places_details"] + ts * (1 - STOP_SHARE) * prices["google_places_text_search"]
           + calls.get("google_routes_matrix_element", 0) * prices["google_routes_matrix_element"]
           + calls.get("tavily_search", 0) * prices["tavily_search"])
    return usd * share


def monthly(per_day: int, model_usd: float, calls: dict[str, float], prices: dict, free: dict, use_free: bool) -> float:
    """USD a month: models, plus data with the places table warm, minus Google's free calls if use_free."""
    n = per_day * 30
    ts = calls.get("google_places_text_search", 0) * WARM_SHARE * n
    use = {"google_places_details": ts * STOP_SHARE, "google_places_text_search": ts * (1 - STOP_SHARE),
           "google_routes_matrix_element": calls.get("google_routes_matrix_element", 0) * WARM_SHARE * n}
    google = sum(max(0, q - (free.get(k, 0) if use_free else 0)) * prices[k] for k, q in use.items())
    tavily = calls.get("tavily_search", 0) * WARM_SHARE * n * prices["tavily_search"]
    return n * model_usd + google + tavily


def report(run: str = "baseline-v9") -> str:
    m, inr = measure(run), _rate()
    prices = yaml.safe_load((CONFIG_DIR / "prices.yaml").read_text(encoding="utf-8"))
    free = {k: v for k, v in yaml.safe_load((CONFIG_DIR / "free_allowance.yaml").read_text(encoding="utf-8")).items()
            if isinstance(v, int)}
    c = m["calls"]
    def rs(usd): return f"₹{usd * inr:,.2f}"
    def rm(usd): return f"₹{usd * inr:,.0f}"
    lines = [
        "# Morning Brief: cost and time per brief", "",
        f"Models measured on the 22-day test set (run `{m['run']}`, including the judge and fixes). Data priced at",
        "Google's India pricing (config/prices.yaml) with the places table; free monthly calls from",
        f"config/free_allowance.yaml. 1 USD = ₹{inr:g}" + (f" (checked {_app().get('inr_per_usd_checked')})." if _app().get("inr_per_usd_checked") else "."), "",
        "## Per brief", "", "| | per brief |", "|---|---|",
        f"| Models | **{rs(m['model_usd'])}** |",
        f"| Data, first traveller to these places | {rs(data_usd(c, prices))} |",
        f"| Data, places table warm | {rs(data_usd(c, prices, WARM_SHARE))} |", "",
        "## Time per brief (the batch prepares briefs ahead of the send time)", "",
        f"median {m['p50_s']:.1f} s · 95th percentile {m['p95_s']:.1f} s · slowest {m['max_s']:.1f} s · limit 90 s", "",
        "## Per month (30 days)", "",
        "| Travellers a day | With the free allowance | Worst case: allowance already used by other apps |", "|---|---|---|",
    ]
    for n in (50, 100):
        lines.append(f"| {n} | {rm(monthly(n, m['model_usd'], c, prices, free, True))} | "
                     f"{rm(monthly(n, m['model_usd'], c, prices, free, False))} |")
    lines += ["", "Google's free allowance is per billing account, so if another app on the same account already uses",
              "it, the worst-case column applies. Full sheet: docs/cost_sheet.md."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(report(*sys.argv[1:]))
