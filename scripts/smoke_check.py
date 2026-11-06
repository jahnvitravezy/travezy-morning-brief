"""End-to-end check of the running service, the way Travezy's backend uses it.

    docker compose up -d --build
    docker compose run --rm api python -m mb.batch --at 2026-11-21T02:00:00Z --window 120 --scenarios
    python scripts/smoke_check.py

Reads MB_API_KEY from .env (never prints it). Prints PASS or FAIL for each step; exit code 1 on any FAIL.
"""
import sys

import httpx
from decouple import config

BASE = "http://localhost:8000"
KEY = {"X-API-Key": config("MB_API_KEY")}
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail and not ok else ''}")


with httpx.Client(base_url=BASE, timeout=120) as c:
    r = c.get("/health")
    check("/health is open and says only ok", r.status_code == 200 and r.json() == {"ok": True}, r.text[:80])

    check("/trips without a key is refused", c.get("/trips").status_code == 401)
    check("/trips with a wrong key is refused", c.get("/trips", headers={"X-API-Key": "wrong"}).status_code == 401)
    r = c.get("/trips", headers=KEY)
    check("/trips with the key works", r.status_code == 200 and len(r.json()) > 0, r.text[:80])

    r = c.get("/status")
    check("/status (spend) needs the key", r.status_code == 401)
    r = c.get("/status", headers=KEY)
    s = r.json() if r.status_code == 200 else {}
    check("/status shows the monthly budget and model warnings", "budget" in s and "model_warnings" in s, r.text[:80])

    window = {"due_from": "2026-11-21T00:00:00Z", "due_to": "2026-11-21T06:00:00Z", "include_sent": "true"}
    r = c.get("/outbox", params=window, headers=KEY)
    due = r.json() if r.status_code == 200 else []
    check("/outbox by send time lists the prepared briefs", r.status_code == 200 and len(due) > 0,
          "run the batch command first" if r.status_code == 200 else r.text[:80])
    if due:
        item = due[0]
        check("each outbox row has a brief_id and a title", bool(item.get("brief_id")) and bool(item.get("title")))
        bid = item["brief_id"]
        r = c.get(f"/briefs/{bid}", headers=KEY)
        b = r.json().get("brief", {}) if r.status_code == 200 else {}
        check("/briefs/{id} returns the stored brief", b.get("brief_id") == bid, r.text[:80])
        check("the title names the day, not 'Day N in'", not b.get("notification", {}).get("title", "Day ").startswith("Day "))
        check("the badge covers only the AI parts",
              b.get("confidence", {}).get("applies_to") == ["heads_ups", "surprises", "local_tip"])
        check("POST /briefs/{id}/sent works", c.post(f"/briefs/{bid}/sent", headers=KEY).status_code == 200)
        check("marking sent twice is safe", c.post(f"/briefs/{bid}/sent", headers=KEY).status_code == 200)
        r = c.get("/outbox", params={k: v for k, v in window.items() if k != "include_sent"}, headers=KEY)
        check("a sent brief leaves the due list", bid not in [x["brief_id"] for x in r.json()], r.text[:80])
        r = c.get(f"/preview/{item['date']}/{item['trip_id']}", headers=KEY)
        check("/preview renders with the header key", r.status_code == 200 and "From your itinerary" in r.text)
        check("/preview without a key is refused", c.get(f"/preview/{item['date']}/{item['trip_id']}").status_code == 401)
    check("/briefs/{unknown} is a 404", c.get("/briefs/nope", headers=KEY).status_code == 404)

    body = {"trip_id": "dubai_up01", "date": "2026-11-21", "weather_scenario": "dubai_clear_warm"}
    check("POST /brief without a key is refused", c.post("/brief", json=body).status_code == 401)
    r = c.post("/brief", json=body, headers=KEY)
    b = r.json() if r.status_code == 200 else {}
    check("POST /brief writes a brief with a UTC send time", bool(b.get("send_at_utc")), r.text[:120])
    check("a date not on the trip is refused",
          c.post("/brief", json=body | {"date": "2026-11-30"}, headers=KEY).status_code in (400, 404, 422))

print(f"\n{sum(results)} of {len(results)} passed")
sys.exit(0 if all(results) else 1)
