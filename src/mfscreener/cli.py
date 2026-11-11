from __future__ import annotations

from datetime import date
from typing import Callable

import typer

from mfscreener.amfi.history import (
    AMFI_OPEN_ENDED_START,
    backfill_recent_months,
    backfill_since_2006,
    backfill_thin_schemes_via_fallback,
)
from mfscreener.amfi.navall import sync_nav_daily
from mfscreener.amfi.ter import sync_aum_from_latest_nav, sync_ter_month
from mfscreener.db import finish_ingestion, run_migrations, start_ingestion
from mfscreener.jobs.benchmarks_risk import compute_risk_and_ranks, sync_benchmarks
from mfscreener.jobs.compute_returns import compute_returns_and_screener
from mfscreener.jobs.holdings_enrich import sync_holdings_for_active_families

app = typer.Typer(help="MF Screener batch jobs (AMFI ingest, compute, enrich)")


def _run_job(name: str, fn: Callable[[], dict]) -> None:
    run_id = start_ingestion(name)
    try:
        stats = fn()
        finish_ingestion(run_id, status="success", stats=stats)
        typer.echo(f"{name}: {stats}")
    except Exception as exc:  # noqa: BLE001
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
) -> None:
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
    _run_job("sync_ter", lambda: sync_ter_month(date.today().replace(day=1)))


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
    """AMFI NAV + full history backfill + mfapi/finapi gap-fill + metrics."""
    _run_job("sync_nav_daily", sync_nav_daily)
    if not skip_full_history:
        _run_job("backfill_nav", lambda: backfill_since_2006(resume=True))
        _run_job(
            "backfill_nav_fallback",
            lambda: backfill_thin_schemes_via_fallback(min_nav_points=252),
        )
    _run_job("compute_returns", compute_returns_and_screener)
    _run_job("sync_benchmarks", lambda: sync_benchmarks(days=5))
    _run_job("compute_risk", compute_risk_and_ranks)
    typer.echo(f"Pipeline complete (history from {AMFI_OPEN_ENDED_START}).")


@app.command("run-nightly")
def run_nightly() -> None:
    """Full pipeline: NAV → returns → benchmarks → risk."""
    for cmd in (
        sync_nav_daily,
        compute_returns_and_screener,
        sync_benchmarks,
        compute_risk_and_ranks,
    ):
        run_id = start_ingestion(cmd.__name__)
        try:
            stats = cmd() if cmd != sync_benchmarks else sync_benchmarks(5)
            finish_ingestion(run_id, status="success", stats=stats)
        except Exception as exc:  # noqa: BLE001
            finish_ingestion(run_id, status="failed", error=str(exc))
            typer.echo(f"Nightly failed at {cmd.__name__}: {exc}", err=True)
            raise typer.Exit(1) from exc
    typer.echo("Nightly pipeline completed.")


if __name__ == "__main__":
    app()
