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


def measure(run: str = "cmp-baseline-gpt4o", ledger: Path = LEDGER, runs: Path = RUNS) -> dict:
    recs = [json.loads(l) for l in (runs / run / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    lat = sorted(r["latency_ms"] / 1000 for r in recs)
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
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
            "batch_briefs": len(batch_briefs)}


def report(run: str = "cmp-baseline-gpt4o") -> str:
    m, inr = measure(run), _rate()
    prices = yaml.safe_load((CONFIG_DIR / "prices.yaml").read_text(encoding="utf-8"))
    free_text_search = 1000 * prices["google_places_text_search"] * inr
    free_routes = 10000 * prices["google_routes_matrix_element"] * inr
    data = m["data_cold_usd"] or 0.0
    def rs(usd): return f"₹{usd * inr:,.2f}"
    def month(per_day, usd): return f"₹{per_day * 30 * usd * inr:,.0f}"
    lines = [
        "# Morning Brief: cost and time per brief", "",
        f"Measured on the 22-day test set (run `{m['run']}`: GPT-4o for the three writing agents, gpt-4o-mini judge,",
        f"including fix attempts). 1 USD = ₹{inr:g}{f" (checked {_app().get('inr_per_usd_checked')})" if _app().get("inr_per_usd_checked") else ""}. Per-call data prices from config/prices.yaml.", "",
        "## Per brief", "",
        "| | per brief |", "|---|---|",
        f"| Models (4 agents, incl. judge and fixes) | **{rs(m['model_usd'])}** |",
        f"| Data, cold (Places, Routes, web search, nothing cached) | {rs(data)} |",
        *( [f"| Data, as measured in the batch run ({m['batch_briefs']} briefs, partly cached) | {rs(m['data_batch_usd'])} |"]
           if m["data_batch_usd"] else []),
        f"| **Total, cold** | **{rs(m['model_usd'] + data)}** |", "",
        "## Time per brief (models only; the batch prepares briefs ahead of the send time)", "",
        f"median {m['p50_s']:.1f} s · 95th percentile {m['p95_s']:.1f} s · slowest {m['max_s']:.1f} s · limit 90 s", "",
        "## Per month (30 days)", "",
        "| Travellers a day | Models only | Models + data, no cache | data 50% cached | data 80% cached |",
        "|---|---|---|---|---|",
    ]
    for n in (50, 100):
        lines.append(f"| {n} | {month(n, m['model_usd'])} | {month(n, m['model_usd'] + data)} | "
                     f"{month(n, m['model_usd'] + data * .5)} | {month(n, m['model_usd'] + data * .2)} |")
    lines += ["", "## Notes", "",
              "- Google Places is most of the cost. Travellers on the same itinerary share hotels and sights, so a cache",
              "  shared across travellers (opening hours kept for a few days) should remove most repeat lookups; the cache",
              "  levels above are estimates until measured with real traffic.",
              "- Prices checked against Google's pricing page on 29 Oct 2026 (Text Search Enterprise $35 per 1,000).",
              f"  Google's free monthly allowances are not deducted above: 1,000 Text Search Enterprise calls (about ₹{free_text_search:,.0f})",
              f"  and 10,000 Route Matrix elements (about ₹{free_routes:,.0f}) a month.",
              "- Claude Sonnet in all three writing roles scored the same as GPT-4o but cost about 3.7x as much in models.",
              "- GPT-4.1 mini as Surprise finder scored within noise of GPT-4o at a lower cost (to confirm with the chosen judge)."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(report(*sys.argv[1:]))
