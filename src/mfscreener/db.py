from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from mfscreener.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().database_url,
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextmanager
def connection() -> Iterator[psycopg.Connection[Any]]:
    with get_pool().connection() as conn:
        yield conn


def run_migrations() -> None:
    migration = Path(__file__).resolve().parents[2] / "migrations" / "001_schema.sql"
    sql = migration.read_text(encoding="utf-8")
    with connection() as conn:
        conn.execute(sql)
        conn.commit()


def start_ingestion(job_name: str) -> int:
    with connection() as conn:
        row = conn.execute(
            """
            INSERT INTO ingestion_runs (job_name, status)
            VALUES (%s, 'running')
            RETURNING id
            """,
            (job_name,),
        ).fetchone()
        conn.commit()
        assert row is not None
        return int(row["id"])


def finish_ingestion(
    run_id: int,
    *,
    status: str,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    import json

    with connection() as conn:
        conn.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = NOW(), status = %s, stats = %s::jsonb, error = %s
            WHERE id = %s
            """,
            (status, json.dumps(stats or {}), error, run_id),
        )
        conn.commit()
