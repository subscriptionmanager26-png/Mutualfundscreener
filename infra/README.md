# Hosted storage (server-side)

Production data **does not** live on user laptops. Provision:

1. **PostgreSQL 16 + TimescaleDB** (≥8 GB volume) — all NAV, metrics, screener tables.
2. **S3-compatible bucket** — raw AMFI `NAVAll`, TER workbooks, history chunks.

## Local / agent stack

```bash
docker compose up -d db minio minio-init
cp .env.example .env
pip install -e ".[dev]"
mf-job migrate
```

## Railway (recommended cloud)

1. Authenticate Railway MCP or CLI and create a project.
2. Add **PostgreSQL** plugin and **Bucket**; set service variables from `.env.example`.
3. Deploy API/worker from this repo (`railway.toml`).
4. Schedule nightly: `mf-job run-nightly` (Railway cron or GitLab scheduled pipeline).

```bash
railway variables set DATABASE_URL=... S3_ENDPOINT=... S3_ACCESS_KEY=... S3_SECRET_KEY=... S3_BUCKET=...
```

Worker cron example (GitLab CI):

```yaml
sync_nav:
  script: mf-job sync-nav-daily && mf-job compute-returns && mf-job compute-risk
  only:
    - schedules
```
