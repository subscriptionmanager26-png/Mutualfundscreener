from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

from mfscreener.amfi.fallback_nav import ExternalNavError, fetch_nav_with_fallback
from mfscreener.config import get_settings
from mfscreener.db import connection
from mfscreener.storage import archive_raw

logger = logging.getLogger(__name__)

HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
AMFI_OPEN_ENDED_START = date(2006, 4, 1)
PROGRESS_FILE = Path(".data/backfill_progress.json")


def _format_amfi_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def download_history_chunk(
    frmdt: date,
    todt: date,
    scheme_type: int = 1,
    *,
    max_attempts: int = 6,
) -> str:
    params = {
        "tp": str(scheme_type),
        "frmdt": _format_amfi_date(frmdt),
        "todt": _format_amfi_date(todt),
    }
    url = f"{HISTORY_URL}?{urlencode(params)}"
    headers = {
        "User-Agent": "mf-screener/0.1 (AMFI historical NAV sync)",
        "Accept": "text/plain,text/csv,*/*",
    }
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            with httpx.Client(timeout=300.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
            if resp.status_code in (429, 502, 503, 504):
                raise httpx.HTTPStatusError(
                    "throttled",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            text = resp.text
            if len(text) < 200 or text.lstrip().lower().startswith("<!doctype"):
                raise ValueError("AMFI returned HTML/empty payload")
            archive_raw(
                source="amfi-history",
                filename=f"nav_history_tp{scheme_type}_{frmdt}_{todt}.txt",
                body=text.encode("utf-8", errors="replace"),
                content_type="text/plain",
            )
            return text
        except (httpx.HTTPError, ValueError) as exc:
            last_err = exc
            sleep_s = min(120, (2**attempt) + random.uniform(0.5, 2.0))
            logger.warning("AMFI chunk %s..%s attempt %s: %s; sleep %.1fs", frmdt, todt, attempt + 1, exc, sleep_s)
            time.sleep(sleep_s)
    raise RuntimeError(f"AMFI chunk {frmdt}..{todt} failed after {max_attempts} tries: {last_err}")


def parse_history_text(text: str) -> list[tuple[int, date, float]]:
    out: list[tuple[int, date, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or ";" not in line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8:
            continue
        if parts[0].lower() == "scheme code":
            continue
        try:
            code = int(parts[0])
            nav = float(parts[6])
            nav_date = datetime.strptime(parts[7], "%d-%b-%Y").date()
        except (ValueError, IndexError):
            continue
        if nav <= 0:
            continue
        out.append((code, nav_date, nav))
    return out


def _persist_nav_rows(rows: list[tuple[int, date, float, str]]) -> int:
    if not rows:
        return 0
    inserted = 0
    codes = sorted({r[0] for r in rows})
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO scheme_master (scheme_code, amc, scheme_name)
                VALUES (%s, '', %s)
                ON CONFLICT (scheme_code) DO NOTHING
                """,
                [(code, f"Scheme {code}") for code in codes],
            )
            cur.executemany(
                """
                INSERT INTO nav_daily (scheme_code, nav_date, nav, source)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                rows,
            )
            inserted = max(cur.rowcount, 0)
        conn.execute(
            """
            UPDATE scheme_master sm SET
                first_nav_date = agg.min_d,
                last_nav_date = agg.max_d,
                last_nav = agg.last_nav
            FROM (
                SELECT scheme_code,
                    MIN(nav_date) AS min_d,
                    MAX(nav_date) AS max_d,
                    (ARRAY_AGG(nav ORDER BY nav_date DESC))[1] AS last_nav
                FROM nav_daily
                WHERE scheme_code = ANY(%s)
                GROUP BY scheme_code
            ) agg
            WHERE sm.scheme_code = agg.scheme_code
            """,
            (codes,),
        )
        conn.commit()
    return inserted


def backfill_chunk(frmdt: date, todt: date) -> dict[str, int | str]:
    text = download_history_chunk(frmdt, todt)
    parsed = parse_history_text(text)
    rows = [(c, d, n, "amfi-history") for c, d, n in parsed]
    inserted = _persist_nav_rows(rows)
    return {
        "source": "amfi",
        "parsed_rows": len(parsed),
        "inserted": inserted,
        "schemes": len({c for c, _, _ in parsed}),
        "from": frmdt.isoformat(),
        "to": todt.isoformat(),
    }


def _load_progress() -> set[str]:
    if not PROGRESS_FILE.exists():
        return set()
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        return set(data.get("completed_chunks", []))
    except json.JSONDecodeError:
        return set()


def _mark_progress(frmdt: date, todt: date) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    done = _load_progress()
    key = f"{frmdt.isoformat()}_{todt.isoformat()}"
    done.add(key)
    PROGRESS_FILE.write_text(
        json.dumps({"completed_chunks": sorted(done)}, indent=2),
        encoding="utf-8",
    )


def backfill_range(
    start: date,
    end: date,
    *,
    chunk_days: int = 31,
    resume: bool = True,
) -> list[dict[str, int | str]]:
    results: list[dict[str, int | str]] = []
    completed = _load_progress() if resume else set()
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        key = f"{cursor.isoformat()}_{chunk_end.isoformat()}"
        if key in completed:
            cursor = chunk_end + timedelta(days=1)
            continue
        try:
            stats = backfill_chunk(cursor, chunk_end)
            results.append(stats)
            _mark_progress(cursor, chunk_end)
        except RuntimeError:
            # Split wide chunk once before falling back to per-scheme APIs.
            if chunk_days > 7:
                mid = cursor + timedelta(days=chunk_days // 2)
                results.extend(backfill_range(cursor, mid - timedelta(days=1), chunk_days=chunk_days // 2, resume=resume))
                results.extend(backfill_range(mid, chunk_end, chunk_days=chunk_days // 2, resume=resume))
            else:
                results.append(
                    {
                        "source": "amfi-failed",
                        "from": cursor.isoformat(),
                        "to": chunk_end.isoformat(),
                        "parsed_rows": 0,
                        "inserted": 0,
                        "schemes": 0,
                    }
                )
        cursor = chunk_end + timedelta(days=1)
    return results


def backfill_since_2006(*, chunk_days: int = 31, resume: bool = True) -> dict[str, object]:
    end = date.today()
    chunks = backfill_range(AMFI_OPEN_ENDED_START, end, chunk_days=chunk_days, resume=resume)
    inserted = sum(int(c.get("inserted", 0)) for c in chunks)
    return {
        "start": AMFI_OPEN_ENDED_START.isoformat(),
        "end": end.isoformat(),
        "chunks_ok": len(chunks),
        "rows_inserted": inserted,
    }


def backfill_recent_months(months: int = 3) -> list[dict[str, int | str]]:
    end = date.today()
    start = end - timedelta(days=months * 31)
    return backfill_range(start, end, chunk_days=31, resume=False)


def backfill_thin_schemes_via_fallback(
    *,
    min_nav_points: int = 252,
    active_only: bool = True,
    limit: int | None = None,
    sleep_s: float = 0.15,
) -> dict[str, int]:
    """Use mfapi.in, then finapi, for schemes still missing deep history after AMFI bulk."""
    with connection() as conn:
        q = """
            SELECT sm.scheme_code, COUNT(nd.nav_date) AS n
            FROM scheme_master sm
            LEFT JOIN nav_daily nd ON nd.scheme_code = sm.scheme_code
            WHERE (%s = FALSE OR sm.is_active)
            GROUP BY sm.scheme_code
            HAVING COUNT(nd.nav_date) < %s
            ORDER BY COUNT(nd.nav_date) ASC, sm.scheme_code
        """
        params: list[object] = [active_only, min_nav_points]
        sql = q
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()

    ok = failures = inserted_total = 0
    for row in rows:
        code = int(row["scheme_code"])
        try:
            series, source = fetch_nav_with_fallback(code)
            payload = [(code, d, n, source) for d, n in series]
            inserted = _persist_nav_rows(payload)
            inserted_total += inserted
            ok += 1
        except (ExternalNavError, RuntimeError) as exc:
            logger.warning("fallback failed %s: %s", code, exc)
            failures += 1
        time.sleep(sleep_s)
    return {
        "candidates": len(rows),
        "ok": ok,
        "failures": failures,
        "rows_inserted": inserted_total,
    }
