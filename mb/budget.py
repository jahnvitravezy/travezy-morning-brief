"""Tracks every paid call and enforces the agreed usage budget.

Every model or API call appends one line to an append-only ledger. A run checks its
estimated cost before it starts and refuses to go past the cap.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from mb.config import CONFIG_DIR, Config, load_config

LEDGER = Path(__file__).resolve().parent.parent / "data" / "usage" / "ledger.jsonl"


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, limit_usd: float, prices: dict[str, float], ledger: Path = LEDGER,
                 warn_at: float = 0.8):
        self.limit_usd = limit_usd
        self.prices = prices
        self.ledger = ledger
        self.warn_at = warn_at

    @classmethod
    def from_config(cls, cfg: Config | None = None, ledger: Path = LEDGER,
                    prices_path: Path = CONFIG_DIR / "prices.yaml") -> "Budget":
        cfg = cfg or load_config()
        prices = yaml.safe_load(prices_path.read_text(encoding="utf-8"))
        return cls(cfg.app.budget_inr / cfg.app.inr_per_usd, prices, ledger)

    def spent_usd(self) -> float:
        if not self.ledger.exists():
            return 0.0
        with self.ledger.open(encoding="utf-8") as f:
            return sum(json.loads(line)["cost_usd"] for line in f if line.strip())

    def remaining_usd(self) -> float:
        return self.limit_usd - self.spent_usd()

    def check(self, estimate_usd: float) -> None:
        """Call before a run. Raises if the run could take spend past the cap."""
        spent = self.spent_usd()
        if spent + estimate_usd > self.limit_usd:
            raise BudgetExceeded(
                f"Run needs ~${estimate_usd:.2f}, only ${self.limit_usd - spent:.2f} of "
                f"${self.limit_usd:.2f} left. Check with Sourabh before going over."
            )

    def record(self, *, run_id: str, kind: str, item: str, cost_usd: float,
               units: dict | None = None) -> dict:
        entry = {"ts": time.time(), "run_id": run_id, "kind": kind, "item": item,
                 "cost_usd": round(cost_usd, 6), "units": units or {}}
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def record_call(self, *, run_id: str, sku: str, n: int = 1) -> dict:
        """For priced API calls (Places, Routes, search). Unknown SKUs are errors, not free."""
        if sku not in self.prices:
            raise ValueError(f"No price for '{sku}' in prices.yaml")
        return self.record(run_id=run_id, kind="api", item=sku,
                           cost_usd=self.prices[sku] * n, units={"calls": n})

    def status(self) -> dict:
        spent = self.spent_usd()
        pct = spent / self.limit_usd
        return {"spent_usd": round(spent, 2), "limit_usd": round(self.limit_usd, 2),
                "used_pct": round(100 * pct, 1), "warning": pct >= self.warn_at}


if __name__ == "__main__":
    print(Budget.from_config().status())