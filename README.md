# Mutual Fund Screener

AMFI-first mutual fund screener for India: ingest NAV/TER on **hosted** Postgres + object storage, compute returns and risk, expose a filterable HTTP API.

Data stays **server-side** (TimescaleDB + S3/MinIO), not on the user's laptop.

## Quick start

```bash
docker compose up -d db minio minio-init
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
mf-job migrate
mf-job sync-nav-daily
mf-job compute-returns
uvicorn mfscreener.api.main:app --reload --port 8080
```

Open `http://127.0.0.1:8080/api/v1/screener?plan=direct&option=growth`.

## Jobs

| Command | Purpose |
|---------|---------|
| `mf-job migrate` | Apply Timescale schema |
| `mf-job sync-nav-daily` | AMFI `NAVAll.txt` → DB + raw archive |
| `mf-job backfill-nav` | Recent AMFI history chunks |
| `mf-job sync-ter` | AMFI TER workbook |
| `mf-job compute-returns` | Trailing returns → `screener_wide` |
| `mf-job sync-benchmarks` | NSE index closes (best-effort) |
| `mf-job compute-risk` | Beta, Sharpe, MDD, category ranks |
| `mf-job sync-holdings` | Optional mfdata.in holdings enrichment |
| `mf-job run-nightly` | NAV + returns + benchmarks + risk |

## API

- `GET /api/v1/screener` — filter/sort/paginate
- `GET /api/v1/schemes/{code}` — detail + recent NAV
- `GET /api/v1/schemes/{code}/nav` — NAV history
- `GET /api/v1/compare?codes=` — compare up to 10 schemes
- `GET /api/v1/overlap?code_a=&code_b=` — holdings overlap
- `GET /api/v1/stats` — freshness counters

## Cloud deploy

See [infra/README.md](infra/README.md) for Railway Postgres + bucket setup.
