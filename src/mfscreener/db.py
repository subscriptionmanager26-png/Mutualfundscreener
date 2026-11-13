from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from mfscreener.config import get_settings

_pool: ConnectionPool | None = None

LOCAL_CLUSTER = ("16", "main")


def ensure_local_postgres(timeout_s: int = 60) -> None:
    """Start the local cluster if it is down (Cloud Agent VMs restore it stopped from the snapshot)."""
    conninfo = conninfo_to_dict(get_settings().database_url)
    if conninfo.get("host", "localhost") not in ("localhost", "127.0.0.1"):
        return
    try:
        psycopg.connect(get_settings().database_url, connect_timeout=3).close()
        return
    except psycopg.OperationalError:
        pass
    subprocess.run(["sudo", "pg_ctlcluster", *LOCAL_CLUSTER, "start"], check=False)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            psycopg.connect(get_settings().database_url, connect_timeout=3).close()
            return
        except psycopg.OperationalError:
            time.sleep(1)
    raise RuntimeError("Local PostgreSQL did not come up; run `sudo pg_ctlcluster 16 main start`")


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
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    with connection() as conn:
        for migration in sorted(migrations_dir.glob("*.sql")):
            conn.execute(migration.read_text(encoding="utf-8"))
        conn.commit()


def get_state(key: str) -> str | None:
    with connection() as conn:
        row = conn.execute("SELECT value FROM pipeline_state WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_state (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (key, value),
        )
        conn.commit()


def nav_row_count() -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT reltuples::bigint AS n FROM pg_class WHERE relname = 'nav_daily'"
        ).fetchone()
    return max(int(row["n"]) if row else 0, 0)


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
