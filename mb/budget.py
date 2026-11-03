"""Tracks every paid call against a monthly budget, with alerts and one hard stop.

Every model or API call is recorded in a ledger: the usage_ledger table in Postgres when
DATABASE_URL is set (shared by every copy of the service, so the count never splits), or
data/usage/ledger.jsonl locally. Spend is counted per calendar month (UTC).

- Alerts at the percentages in config/app.yaml (budget_alert_pct, e.g. 50 and 80): each fires
  once per month, as a log warning and, if MB_ALERT_WEBHOOK is set, a POST to that URL.
- One hard stop at budget_stop_pct (100): a run that could go past it is refused.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path

import yaml

from mb.config import CONFIG_DIR, Config, load_config

LEDGER = Path(__file__).resolve().parent.parent / "data" / "usage" / "ledger.jsonl"
log = logging.getLogger("mb.budget")


class BudgetExceeded(RuntimeError):
    pass


def month_start(now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class FileLedger:
    def __init__(self, path: Path = LEDGER):
        self.path = path

    def add(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def spent_since(self, since: dt.datetime) -> float:
        if not self.path.exists():
            return 0.0
        t0 = since.timestamp()
        with self.path.open(encoding="utf-8") as f:
            return sum(e["cost_usd"] for e in map(json.loads, filter(str.strip, f)) if e["ts"] >= t0)

    def first_alert(self, month: dt.date, pct: int) -> bool:
        return True                     # one process: crossing a line happens once anyway


class PgLedger:
    def __init__(self):
        from mb import db
        self.conn = db.conn()

    def add(self, entry: dict) -> None:
        from psycopg.types.json import Jsonb
        self.conn.execute("INSERT INTO usage_ledger (run_id, kind, item, cost_usd, units) VALUES (%s,%s,%s,%s,%s)",
                          (entry["run_id"], entry["kind"], entry["item"], entry["cost_usd"], Jsonb(entry["units"])))

    def spent_since(self, since: dt.datetime) -> float:
        return float(self.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger WHERE ts >= %s",
                                       (since,)).fetchone()[0])

    def first_alert(self, month: dt.date, pct: int) -> bool:
        cur = self.conn.execute("INSERT INTO budget_alerts (month, pct) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                                (month, pct))
        return cur.rowcount == 1        # only the copy that records it first sends the alert


def default_ledger():
    from mb import db
    return PgLedger() if db.enabled() else FileLedger()


class Budget:
    def __init__(self, limit_usd: float, prices: dict[str, float], ledger=None,
                 alert_pct: tuple[int, ...] = (50, 80), stop_pct: int = 100):
        self.limit_usd = limit_usd
        self.prices = prices
        self.ledger = FileLedger(ledger) if isinstance(ledger, Path) else (ledger or default_ledger())
        self.alert_pct = sorted(alert_pct)
        self.stop_pct = stop_pct
        self.alerts: list[str] = []

    @classmethod
    def from_config(cls, cfg: Config | None = None, ledger=None,
                    prices_path: Path = CONFIG_DIR / "prices.yaml") -> "Budget":
        cfg = cfg or load_config()
        prices = yaml.safe_load(prices_path.read_text(encoding="utf-8"))
        a = cfg.app
        return cls(a.monthly_budget_inr / a.inr_per_usd, prices, ledger, tuple(a.budget_alert_pct), a.budget_stop_pct)

    @property
    def stop_usd(self) -> float:
        return self.limit_usd * self.stop_pct / 100

    def spent_usd(self) -> float:
        """Spent this calendar month (UTC)."""
        return self.ledger.spent_since(month_start())

    def remaining_usd(self) -> float:
        return self.stop_usd - self.spent_usd()

    def check(self, estimate_usd: float) -> None:
        """Call before a run. Raises if the run could take this month's spend past the hard stop."""
        spent = self.spent_usd()
        if spent + estimate_usd > self.stop_usd:
            raise BudgetExceeded(
                f"Run needs ~${estimate_usd:.2f}, only ${self.stop_usd - spent:.2f} of this month's "
                f"${self.stop_usd:.2f} left. Raise monthly_budget_inr in config/app.yaml to go on.")

    def record(self, *, run_id: str, kind: str, item: str, cost_usd: float,
               units: dict | None = None) -> dict:
        before = self.spent_usd()
        entry = {"ts": time.time(), "run_id": run_id, "kind": kind, "item": item,
                 "cost_usd": round(cost_usd, 6), "units": units or {}}
        self.ledger.add(entry)
        self._alert(before, before + entry["cost_usd"])
        return entry

    def record_call(self, *, run_id: str, sku: str, n: int = 1) -> dict:
        """For priced API calls (Places, Routes, search). Unknown SKUs are errors, not free."""
        if sku not in self.prices:
            raise ValueError(f"No price for '{sku}' in prices.yaml")
        return self.record(run_id=run_id, kind="api", item=sku,
                           cost_usd=self.prices[sku] * n, units={"calls": n})

    def _alert(self, before: float, after: float) -> None:
        for pct in self.alert_pct:
            line = self.limit_usd * pct / 100
            if before < line <= after and self.ledger.first_alert(month_start().date(), pct):
                msg = (f"Morning Brief has used {pct}% of this month's budget "
                       f"(${after:.2f} of ${self.limit_usd:.2f}).")
                self.alerts.append(msg)
                log.warning(msg)
                url = os.environ.get("MB_ALERT_WEBHOOK")
                if url:
                    try:
                        import httpx
                        httpx.post(url, json={"text": msg, "pct": pct}, timeout=5)
                    except Exception as e:           # an alert must never break a brief
                        log.warning("budget alert webhook failed: %s", e)

    def status(self) -> dict:
        spent = self.spent_usd()
        pct = 100 * spent / self.limit_usd
        crossed = [p for p in self.alert_pct if pct >= p]
        return {"month_from": month_start().date().isoformat(), "spent_usd": round(spent, 2),
                "budget_usd": round(self.limit_usd, 2), "used_pct": round(pct, 1),
                "alerts_crossed": crossed, "warning": bool(crossed), "stopped": spent >= self.stop_usd}


if __name__ == "__main__":
    print(Budget.from_config().status())
