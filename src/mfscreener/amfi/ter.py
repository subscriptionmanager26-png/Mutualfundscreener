from __future__ import annotations

import io
from datetime import date, datetime

import httpx
from openpyxl import load_workbook

from mfscreener.config import get_settings
from mfscreener.db import connection
from mfscreener.storage import archive_raw


def _month_param(d: date) -> str:
    return d.strftime("%m-%Y")


def download_ter_workbook(month: date) -> bytes:
    settings = get_settings()
    url = settings.amfi_ter_url_template.format(month=_month_param(month))
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.content
    archive_raw(
        source="amfi-ter",
        filename=f"ter_{_month_param(month)}.xlsx",
        body=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return data


def parse_ter_xlsx(data: bytes) -> list[tuple[int, date, str, float]]:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = wb.active
    rows_iter = sheet.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        return []
    col_map = {str(h).strip().lower(): i for i, h in enumerate(header) if h is not None}
    code_col = col_map.get("scheme code") or col_map.get("schemecode") or 0
    date_col = col_map.get("ter date") or col_map.get("date") or 1
    ter_col = col_map.get("ter (%)") or col_map.get("ter") or 2
    plan_col = col_map.get("plan") or col_map.get("plan type")
    out: list[tuple[int, date, str, float]] = []
    for row in rows_iter:
        if not row or row[0] is None:
            continue
        try:
            code = int(str(row[code_col]).strip())
        except (ValueError, TypeError):
            continue
        raw_date = row[date_col] if isinstance(date_col, int) else None
        ter_raw = row[ter_col] if isinstance(ter_col, int) else None
        if ter_raw is None:
            continue
        try:
            ter = float(ter_raw)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_date, datetime):
            ter_date = raw_date.date()
        elif isinstance(raw_date, date):
            ter_date = raw_date
        else:
            try:
                ter_date = datetime.strptime(str(raw_date).strip()[:11], "%d-%b-%Y").date()
            except ValueError:
                continue
        plan = "unknown"
        if plan_col is not None and row[plan_col]:
            plan = str(row[plan_col]).strip().lower()
            if "direct" in plan:
                plan = "direct"
            elif "regular" in plan:
                plan = "regular"
        out.append((code, ter_date, plan, ter))
    return out


def sync_ter_month(month: date | None = None) -> dict[str, int]:
    month = month or date.today().replace(day=1)
    data = download_ter_workbook(month)
    rows = parse_ter_xlsx(data)
    upserted = 0
    with connection() as conn:
        for code, ter_date, plan, ter in rows:
            conn.execute(
                """
                INSERT INTO scheme_master (scheme_code, amc, scheme_name)
                VALUES (%s, '', %s)
                ON CONFLICT (scheme_code) DO NOTHING
                """,
                (code, f"Scheme {code}"),
            )
            conn.execute(
                """
                INSERT INTO ter_daily (scheme_code, ter_date, plan, ter_pct)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (scheme_code, ter_date, plan) DO UPDATE SET
                    ter_pct = EXCLUDED.ter_pct,
                    ingested_at = NOW()
                """,
                (code, ter_date, plan, ter),
            )
            upserted += 1
        conn.commit()
    return {"ter_rows": upserted, "month": _month_param(month)}


def sync_aum_from_latest_nav() -> dict[str, int]:
    """Placeholder AUM ingest: store latest NAV file date counts until AMFI AUM parser is added."""
    with connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, MAX(last_nav_date) AS d FROM scheme_master WHERE is_active
            """
        ).fetchone()
    return {"active_schemes": int(row["n"]) if row else 0, "as_of": str(row["d"]) if row else None}
