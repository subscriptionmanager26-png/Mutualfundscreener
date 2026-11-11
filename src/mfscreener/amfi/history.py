from __future__ import annotations

from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import httpx

from mfscreener.db import connection
from mfscreener.storage import archive_raw

HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"


def _format_amfi_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def download_history_chunk(frmdt: date, todt: date, scheme_type: int = 1) -> str:
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
    with httpx.Client(timeout=300.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text
    archive_raw(
        source="amfi-history",
        filename=f"nav_history_tp{scheme_type}_{frmdt}_{todt}.txt",
        body=text.encode("utf-8", errors="replace"),
        content_type="text/plain",
    )
    return text


def parse_history_text(text: str) -> list[tuple[int, date, float]]:
    """Parse AMFI bulk history file (semicolon-separated)."""
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


def backfill_chunk(frmdt: date, todt: date) -> dict[str, int]:
    text = download_history_chunk(frmdt, todt)
    rows = parse_history_text(text)
    inserted = 0
    schemes_touched: set[int] = set()
    with connection() as conn:
        for code, nav_date, nav in rows:
            schemes_touched.add(code)
            conn.execute(
                """
                INSERT INTO scheme_master (scheme_code, amc, scheme_name)
                VALUES (%s, '', %s)
                ON CONFLICT (scheme_code) DO NOTHING
                """,
                (code, f"Scheme {code}"),
            )
            cur = conn.execute(
                """
                INSERT INTO nav_daily (scheme_code, nav_date, nav, source)
                VALUES (%s, %s, %s, 'amfi-history')
                ON CONFLICT DO NOTHING
                """,
                (code, nav_date, nav),
            )
            if cur.rowcount:
                inserted += 1
            conn.execute(
                """
                UPDATE scheme_master SET
                    first_nav_date = LEAST(COALESCE(first_nav_date, %s), %s),
                    last_nav_date = GREATEST(COALESCE(last_nav_date, %s), %s),
                    last_nav = CASE WHEN %s >= COALESCE(last_nav_date, %s) THEN %s ELSE last_nav END
                WHERE scheme_code = %s
                """,
                (
                    nav_date,
                    nav_date,
                    nav_date,
                    nav_date,
                    nav_date,
                    nav_date,
                    nav,
                    code,
                ),
            )
        conn.commit()
    return {
        "parsed_rows": len(rows),
        "inserted": inserted,
        "schemes": len(schemes_touched),
        "from": frmdt.isoformat(),
        "to": todt.isoformat(),
    }


def backfill_recent_months(months: int = 3) -> list[dict[str, int]]:
    results = []
    end = date.today()
    start = end - timedelta(days=months * 31)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=30), end)
        results.append(backfill_chunk(cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return results
