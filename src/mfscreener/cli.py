from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import typer

from mfscreener.amfi.history import (
    AMFI_OPEN_ENDED_START,
    backfill_recent_months,
    backfill_since_2006,
    backfill_thin_schemes_via_fallback,
    catch_up_history,
)
from mfscreener.amfi.navall import refresh_active_flags, sync_nav_daily
from mfscreener.amfi.ter import sync_aum_from_latest_nav, sync_ter_month
from mfscreener.db import (
    ensure_local_postgres,
    finish_ingestion,
    nav_row_count,
    run_migrations,
    start_ingestion,
)
from mfscreener.jobs.benchmarks_risk import compute_risk_and_ranks, sync_benchmarks
from mfscreener.jobs.compute_returns import compute_returns_and_screener
from mfscreener.jobs.holdings_enrich import sync_holdings_for_active_families

app = typer.Typer(help="MF Screener batch jobs (AMFI ingest, compute, enrich)")


@app.callback()
def _main() -> None:
    ensure_local_postgres()


FULL_HISTORY_GUARD_ROWS = 1_000_000


def _run_job(name: str, fn: Callable[[], dict]) -> None:
    run_id = start_ingestion(name)
    try:
        stats = fn()
        finish_ingestion(run_id, status="success", stats=stats)
        typer.echo(f"{name}: {stats}")
    except Exception as exc:
        finish_ingestion(run_id, status="failed", error=str(exc))
        typer.echo(f"{name} failed: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("migrate")
def migrate() -> None:
    run_migrations()
    typer.echo("Migrations applied.")


@app.command("sync-nav-daily")
def sync_nav_daily_cmd() -> None:
    _run_job("sync_nav_daily", sync_nav_daily)


@app.command("backfill-nav")
def backfill_nav_cmd(
    full: bool = typer.Option(
        True,
        "--full/--partial",
        help="Full AMFI history from Apr 2006 (default) or partial window",
    ),
    months: int = typer.Option(3, help="When --partial, months of history to pull"),
    chunk_days: int = typer.Option(31, help="Days per AMFI bulk request"),
    resume: bool = typer.Option(True, help="Skip chunks already in .data/backfill_progress.json"),
    force: bool = typer.Option(False, help="Allow a full backfill over an already-loaded database"),
) -> None:
    if full and not force and nav_row_count() >= FULL_HISTORY_GUARD_ROWS:
        typer.echo(
            f"nav_daily already holds ~{nav_row_count():,} rows. Use `mf-job catch-up` to fill "
            "the gap since the last run, or pass --force to re-run the full 2006 backfill.",
            err=True,
        )
        raise typer.Exit(1)

    def _fn() -> dict:
        if full:
            return backfill_since_2006(chunk_days=chunk_days, resume=resume)
        chunks = backfill_recent_months(months=months)
        return {"chunks": len(chunks), "last": chunks[-1] if chunks else {}}

    _run_job("backfill_nav", _fn)


@app.command("backfill-nav-fallback")
def backfill_nav_fallback_cmd(
    min_points: int = typer.Option(252, help="Schemes with fewer NAV days use mfapi/finapi"),
    limit: int | None = typer.Option(None, help="Cap schemes (for testing)"),
) -> None:
    """Fill gaps when AMFI throttles: mfapi.in first, then finapi.upvaly.com."""
    _run_job(
        "backfill_nav_fallback",
        lambda: backfill_thin_schemes_via_fallback(min_nav_points=min_points, limit=limit),
    )


@app.command("compute-returns")
def compute_returns_cmd() -> None:
    _run_job("compute_returns", compute_returns_and_screener)


@app.command("sync-ter")
def sync_ter_cmd() -> None:
    _run_job("sync_ter", _sync_latest_ter)


@app.command("refresh-active")
def refresh_active_cmd() -> None:
    _run_job("refresh_active", refresh_active_flags)


@app.command("sync-aum")
def sync_aum_cmd() -> None:
    _run_job("sync_aum", sync_aum_from_latest_nav)


@app.command("sync-benchmarks")
def sync_benchmarks_cmd(days: int = 5) -> None:
    _run_job("sync_benchmarks", lambda: sync_benchmarks(days=days))


@app.command("compute-risk")
def compute_risk_cmd() -> None:
    _run_job("compute_risk", compute_risk_and_ranks)


@app.command("sync-holdings")
def sync_holdings_cmd(limit: int = 50) -> None:
    _run_job("sync_holdings", lambda: sync_holdings_for_active_families(limit=limit))


@app.command("first-cut")
def first_cut_cmd(
    skip_full_history: bool = typer.Option(
        False,
        "--skip-full-history",
        help="Skip 2006→today bulk (not recommended)",
    ),
) -> None:
    """AMFI NAV + full history backfill + mfapi/finapi gap-fill + metrics, for an empty database."""
    if nav_row_count() >= FULL_HISTORY_GUARD_ROWS:
        typer.echo("Database already loaded; running `catch-up` instead.")
        catch_up_cmd()
        return
    _run_job("sync_nav_daily", sync_nav_daily)
    if not skip_full_history:
        _run_job("backfill_nav", lambda: backfill_since_2006(resume=True))
        _run_job(
            "backfill_nav_fallback",
            lambda: backfill_thin_schemes_via_fallback(min_nav_points=252),
        )
    _run_job("refresh_active", refresh_active_flags)
    _run_job("compute_returns", compute_returns_and_screener)
    _run_job("sync_benchmarks", lambda: sync_benchmarks(days=5))
    _run_job("compute_risk", compute_risk_and_ranks)
    typer.echo(f"Pipeline complete (history from {AMFI_OPEN_ENDED_START}).")


def _sync_latest_ter() -> dict:
    """AMFI publishes a month's TER workbook with a lag; fall back to the previous month."""
    this_month = date.today().replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    errors = []
    for month in (this_month, last_month):
        try:
            stats = sync_ter_month(month)
            if stats["ter_rows"]:
                return stats
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{month:%m-%Y}: {exc}")
    return {"ter_rows": 0, "errors": errors}


@app.command("catch-up")
def catch_up_cmd(
    skip_compute: bool = typer.Option(False, help="Only ingest; do not recompute metrics"),
) -> None:
    """Bring a pre-loaded database up to today: NAV gap since last run, TER, then all metrics."""
    run_migrations()
    _run_job("catch_up_history", catch_up_history)
    _run_job("sync_nav_daily", sync_nav_daily)
    _run_job("refresh_active", refresh_active_flags)
    _run_job("sync_ter", _sync_latest_ter)
    if skip_compute:
        return
    _run_job("compute_returns", compute_returns_and_screener)
    _run_job("sync_benchmarks", lambda: sync_benchmarks(days=5))
    _run_job("compute_risk", compute_risk_and_ranks)
    typer.echo("Catch-up complete.")


@app.command("run-nightly")
def run_nightly() -> None:
    """Alias for `catch-up`."""
    catch_up_cmd(skip_compute=False)


if __name__ == "__main__":
    app()
