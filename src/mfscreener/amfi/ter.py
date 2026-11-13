from __future__ import annotations

import io
import re
from datetime import date, datetime

import httpx
from openpyxl import load_workbook

from mfscreener.config import get_settings
from mfscreener.db import connection
from mfscreener.storage import archive_raw

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_fund_name(name: str) -> str:
    return _NON_ALNUM.sub(" ", name.lower()).strip()


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


def _to_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip()[:11], fmt).date()
        except ValueError:
            continue
    return None


def _to_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def parse_ter_xlsx(data: bytes) -> list[dict[str, object]]:
    """One row per fund per day: AMFI lays out regular and direct Total TER side by side."""
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows_iter = wb.active.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        return []
    cols = {str(h).strip().lower(): i for i, h in enumerate(header) if h is not None}
    name_col = cols.get("scheme name")
    date_col = cols.get("ter date")
    cat_col = cols.get("scheme category")
    reg_col = next((i for h, i in cols.items() if h.startswith("regular") and "total ter" in h), None)
    dir_col = next((i for h, i in cols.items() if h.startswith("direct") and "total ter" in h), None)
    if name_col is None or date_col is None or (reg_col is None and dir_col is None):
        raise ValueError(f"Unrecognised AMFI TER layout: {list(cols)}")
    out: list[dict[str, object]] = []
    for row in rows_iter:
        if not row or row[name_col] is None:
            continue
        ter_date = _to_date(row[date_col])
        if ter_date is None:
            continue
        name = str(row[name_col]).strip()
        out.append(
            {
                "fund_name_norm": normalize_fund_name(name),
                "fund_name": name,
                "ter_date": ter_date,
                "sebi_category": row[cat_col] if cat_col is not None else None,
                "ter_regular_pct": _to_float(row[reg_col]) if reg_col is not None else None,
                "ter_direct_pct": _to_float(row[dir_col]) if dir_col is not None else None,
            }
        )
    return out


def store_ter_rows(rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ter_fund_daily (
                    fund_name_norm, ter_date, fund_name, sebi_category,
                    ter_regular_pct, ter_direct_pct
                ) VALUES (
                    %(fund_name_norm)s, %(ter_date)s, %(fund_name)s, %(sebi_category)s,
                    %(ter_regular_pct)s, %(ter_direct_pct)s
                )
                ON CONFLICT (fund_name_norm, ter_date) DO UPDATE SET
                    ter_regular_pct = EXCLUDED.ter_regular_pct,
                    ter_direct_pct = EXCLUDED.ter_direct_pct,
                    ingested_at = NOW()
                """,
                rows,
            )
        conn.commit()
    return len(rows)


def sync_ter_month(month: date | None = None) -> dict[str, object]:
    month = month or date.today().replace(day=1)
    rows = parse_ter_xlsx(download_ter_workbook(month))
    return {
        "month": _month_param(month),
        "ter_rows": store_ter_rows(rows),
        "funds": len({r["fund_name_norm"] for r in rows}),
    }


def sync_aum_from_latest_nav() -> dict[str, object]:
    """AMFI AUM ingest is not implemented yet; reports the active-scheme count it would cover."""
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(last_nav_date) AS d FROM scheme_master WHERE is_active"
        ).fetchone()
    return {"active_schemes": int(row["n"]) if row else 0, "as_of": str(row["d"]) if row else None}
