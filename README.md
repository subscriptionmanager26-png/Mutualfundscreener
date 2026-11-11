# Mutual Fund Screener

AMFI-first India mutual fund screener. **Base data and metrics live on this machine**: local PostgreSQL for NAV/schemes/`screener_wide`, plus on-disk copies of raw AMFI files under `.data/raw-amfi`. Nothing is deployed to Railway or other cloud storage by default.

## Setup (this host)

PostgreSQL must be running locally with database `mfscreener` (see `scripts/setup-local-postgres.sh` once per machine).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
mf-job migrate
```

## First cut: ingest base data + compute metrics

```bash
mf-job first-cut
```

That runs:

1. **AMFI `NAVAll.txt`** → `scheme_master`, `nav_daily`, raw archive  
2. **~1 month history backfill** (optional depth via `mf-job backfill-nav`)  
3. **Trailing returns** → `screener_wide`  
4. **Risk metrics** (vol, drawdown, beta/Sharpe where benchmark data exists) + category ranks  

Or step by step:

```bash
mf-job sync-nav-daily
mf-job backfill-nav --months 1
mf-job compute-returns
mf-job sync-benchmarks
mf-job compute-risk
```

Serve the API on this host:

```bash
uvicorn mfscreener.api.main:app --host 127.0.0.1 --port 8080
```

`GET http://127.0.0.1:8080/api/v1/screener?plan=direct&option=growth`

## Jobs

| Command | Purpose |
|---------|---------|
| `mf-job migrate` | Apply schema (plain Postgres; Timescale optional) |
| `mf-job first-cut` | NAV + backfill + returns + benchmarks + risk |
| `mf-job sync-nav-daily` | Daily AMFI snapshot |
| `mf-job compute-returns` | Refresh `screener_wide` return columns |
| `mf-job compute-risk` | Vol, MDD, beta, Sharpe, category ranks |
| `mf-job run-nightly` | Incremental daily pipeline |

## API

- `GET /api/v1/screener` — filter/sort (default Direct Growth)
- `GET /api/v1/schemes/{code}` — scheme + metrics + recent NAV
- `GET /api/v1/stats` — row counts and latest NAV date

Data stays on **this computer** (Postgres data directory + `.data/raw-amfi`), not on your laptop unless you clone or sync the repo yourself.
