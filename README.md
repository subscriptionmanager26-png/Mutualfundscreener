# Mutual Fund Screener

AMFI-first India mutual fund screener. NAV history (April 2006 onward), scheme master, TER and computed metrics live in a local PostgreSQL database named `mfscreener`.

## Cloud Agents and automations

The Cloud Agent environment for this repo boots from a snapshot that already contains the loaded database (~25M NAV rows), so agents and automations never rebuild history.

- On boot, `scripts/cloud-agent-start.sh` starts Postgres and prints the `nav_daily` row count (log: `/tmp/cursor/start-user/start-user.log`).
- Begin every automation with:

  ```bash
  source .venv/bin/activate 2>/dev/null || { python3 -m venv .venv && source .venv/bin/activate; }
  pip install -q -e ".[dev]"
  mf-job catch-up
  ```

  `catch-up` backfills only the NAV gap since the stored watermark (`pipeline_state.history_complete_through`), pulls today's `NAVAll.txt` and the latest TER workbook, then recomputes every metric. The gap grows with the snapshot's age; a month takes about a minute.
- Writes made during a run are discarded with the VM. Persist results by committing reports, opening a PR, or posting them elsewhere.
- To move the baked data forward, run `mf-job catch-up` in an agent, take a new environment snapshot, build from it, and save the environment.

`mf-job backfill-nav --full` refuses to run against a loaded database unless `--force` is passed.

## Fresh machine setup

```bash
scripts/setup-local-postgres.sh
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
mf-job migrate
mf-job first-cut        # full 2006→today AMFI backfill; several hours
```

## Jobs

| Command | Purpose |
|---------|---------|
| `mf-job catch-up` | Gap backfill + daily NAV + TER + all metrics (use this on a loaded DB) |
| `mf-job first-cut` | Full load for an empty DB; delegates to `catch-up` if already loaded |
| `mf-job backfill-nav --full` | AMFI bulk history from Apr 2006 (resumable) |
| `mf-job backfill-nav-fallback` | Thin schemes via mfapi.in, then finapi |
| `mf-job sync-nav-daily` | Latest AMFI `NAVAll.txt` |
| `mf-job sync-ter` | AMFI TER workbook (current month, else previous) |
| `mf-job compute-returns` | Returns + TER → `screener_wide` |
| `mf-job sync-benchmarks` | Index-fund proxies for Nifty 50/100/500, Midcap 150, Smallcap 250 |
| `mf-job compute-risk` | Volatility, drawdown, Sharpe, Sortino, beta, alpha, category ranks |

## Metrics in `screener_wide`

| Group | Columns |
|-------|---------|
| Snapshot | `last_nav`, `as_of_date`, `day_change_pct`, `ter_pct`, `nav_days`, `first_nav_date` |
| Returns | `ret_1w`, `ret_1m`, `ret_3m`, `ret_6m`, `ret_ytd` (simple); `ret_1y`, `ret_2y`, `ret_3y`, `ret_5y`, `ret_10y`, `ret_si` (CAGR) |
| Risk | `vol_1y`, `vol_3y`, `mdd_1y`, `mdd_3y`, `mdd_5y`, `sharpe_1y`, `sharpe_3y`, `sortino_3y` |
| Benchmark | `beta_3y`, `alpha_3y`, `benchmark_id` (equity schemes) |
| Peer rank | `rank_ret_1y_category`, `rank_ret_3y_category`, `rank_sharpe_3y_category` (percentile within SEBI category, plan and option; 100 is best) |

Returns are computed from published NAV, so IDCW options understate total return; compare growth options. A horizon is left empty when the scheme's history does not reach back to it.

## API

```bash
uvicorn mfscreener.api.main:app --host 127.0.0.1 --port 8080
```

- `GET /api/v1/screener` — filter/sort (default Direct Growth)
- `GET /api/v1/schemes/{code}` — scheme + metrics + recent NAV
- `GET /api/v1/stats` — row counts and latest NAV date
