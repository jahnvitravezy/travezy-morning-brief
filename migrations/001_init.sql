-- Morning Brief tables, in their own schema (MB_DB_SCHEMA, default morning_brief).
-- Applied once by `python -m mb.db` (the service also runs it on start-up).

CREATE TABLE IF NOT EXISTS outbox (
    trip_id text NOT NULL, date date NOT NULL, timezone text NOT NULL,
    send_at_local text NOT NULL, send_at_utc timestamptz NOT NULL,
    verdict text, score int, badge text, brief jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trip_id, date));

-- Every paid call, shared by every copy of the service, so the budget never splits.
CREATE TABLE IF NOT EXISTS usage_ledger (
    id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
    run_id text NOT NULL, kind text NOT NULL, item text NOT NULL,
    cost_usd numeric(12,6) NOT NULL, units jsonb NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS usage_ledger_ts ON usage_ledger (ts);

-- Each budget alert fires once per month, even with several copies running.
CREATE TABLE IF NOT EXISTS budget_alerts (
    month date NOT NULL, pct int NOT NULL, fired_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (month, pct));

-- Shared cache for weather, routes and search: same request, same answer, no second bill.
CREATE TABLE IF NOT EXISTS cache (
    name text NOT NULL, key text NOT NULL, value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (name, key));

-- Places found once and reused across travellers; details refreshed on a schedule.
CREATE TABLE IF NOT EXISTS places (
    place_id text PRIMARY KEY, name text NOT NULL, lat double precision, lng double precision,
    address text, types text[], rating real, price_level text,
    opening_hours jsonb, details jsonb NOT NULL DEFAULT '{}',
    found_at timestamptz NOT NULL DEFAULT now(),
    details_checked_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS place_searches (
    query_key text PRIMARY KEY, query jsonb NOT NULL, place_ids text[] NOT NULL,
    searched_at timestamptz NOT NULL DEFAULT now());
