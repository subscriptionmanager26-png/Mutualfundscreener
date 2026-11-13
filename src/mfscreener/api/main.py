from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from mfscreener.db import connection, get_pool

app = FastAPI(
    title="Mutual Fund Screener API",
    description="AMFI-first India mutual fund screener",
    version="0.1.0",
)


@app.on_event("shutdown")
def _shutdown_pool() -> None:
    get_pool().close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/stats")
def stats() -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT MAX(nav_date) FROM nav_daily) AS latest_nav_date,
                (SELECT MAX(period_end) FROM aum_periodic) AS latest_aum,
                (SELECT MAX(ter_date) FROM ter_daily) AS latest_ter,
                (SELECT MAX(as_of) FROM holdings_snapshot) AS latest_holdings,
                (SELECT COUNT(*) FROM scheme_master WHERE is_active) AS active_schemes,
                (SELECT COUNT(*) FROM screener_wide) AS screener_rows
            """
        ).fetchone()
    return dict(row or {})


SORT_COLUMNS = {
    "ret_3y",
    "ret_1y",
    "ret_5y",
    "ter_pct",
    "aum_cr",
    "sharpe_3y",
    "mdd_3y",
    "last_nav",
}


@app.get("/api/v1/screener")
def screener(
    sebi_category: str | None = None,
    amc: str | None = None,
    plan: str | None = Query(default="direct"),
    option: str | None = Query(default="growth"),
    min_ret_3y: float | None = None,
    max_ter: float | None = None,
    has_holdings: bool | None = None,
    sort: str = Query(default="ret_3y"),
    order: str = Query(default="desc"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if sort not in SORT_COLUMNS:
        raise HTTPException(status_code=400, detail=f"Invalid sort column: {sort}")
    order_sql = "DESC" if order.lower() == "desc" else "ASC"
    filters = ["1=1"]
    params: list[Any] = []
    if sebi_category:
        filters.append("sebi_category = %s")
        params.append(sebi_category)
    if amc:
        filters.append("amc ILIKE %s")
        params.append(f"%{amc}%")
    if plan:
        filters.append("plan = %s")
        params.append(plan)
    if option:
        filters.append("option = %s")
        params.append(option)
    if min_ret_3y is not None:
        filters.append("ret_3y >= %s")
        params.append(min_ret_3y)
    if max_ter is not None:
        filters.append("ter_pct <= %s")
        params.append(max_ter)
    if has_holdings is not None:
        filters.append("has_holdings = %s")
        params.append(has_holdings)
    where = " AND ".join(filters)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM screener_wide
            WHERE {where}
            ORDER BY {sort} {order_sql} NULLS LAST
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM screener_wide WHERE {where}",
            params,
        ).fetchone()
    return {"count": int(total["c"]) if total else 0, "data": rows}


@app.get("/api/v1/schemes/{scheme_code}")
def scheme_detail(scheme_code: int) -> dict[str, Any]:
    with connection() as conn:
        master = conn.execute(
            "SELECT * FROM scheme_master WHERE scheme_code = %s",
            (scheme_code,),
        ).fetchone()
        if not master:
            raise HTTPException(status_code=404, detail="Scheme not found")
        metrics = conn.execute(
            "SELECT * FROM screener_wide WHERE scheme_code = %s",
            (scheme_code,),
        ).fetchone()
        nav = conn.execute(
            """
            SELECT nav_date, nav FROM nav_daily
            WHERE scheme_code = %s
            ORDER BY nav_date DESC LIMIT 90
            """,
            (scheme_code,),
        ).fetchall()
    return {"scheme": master, "metrics": metrics, "nav_recent": nav}


@app.get("/api/v1/schemes/{scheme_code}/nav")
def scheme_nav(
    scheme_code: int,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(default=252, le=2000),
) -> dict[str, Any]:
    clauses = ["scheme_code = %s"]
    params: list[Any] = [scheme_code]
    if start:
        clauses.append("nav_date >= %s")
        params.append(start)
    if end:
        clauses.append("nav_date <= %s")
        params.append(end)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT nav_date, nav FROM nav_daily
            WHERE {' AND '.join(clauses)}
            ORDER BY nav_date DESC
            LIMIT %s
            """,
            (*params, limit),
        ).fetchall()
    return {"scheme_code": scheme_code, "data": list(reversed(rows))}


@app.get("/api/v1/compare")
def compare(codes: str = Query(description="Comma-separated AMFI scheme codes")) -> dict[str, Any]:
    scheme_codes = [int(c.strip()) for c in codes.split(",") if c.strip()]
    if not scheme_codes or len(scheme_codes) > 10:
        raise HTTPException(status_code=400, detail="Provide 1-10 scheme codes")
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM screener_wide WHERE scheme_code = ANY(%s)
            """,
            (scheme_codes,),
        ).fetchall()
    return {"data": rows}


@app.get("/api/v1/overlap")
def overlap(code_a: int, code_b: int) -> dict[str, Any]:
    with connection() as conn:
        fa = conn.execute(
            "SELECT family_id FROM scheme_master WHERE scheme_code = %s",
            (code_a,),
        ).fetchone()
        fb = conn.execute(
            "SELECT family_id FROM scheme_master WHERE scheme_code = %s",
            (code_b,),
        ).fetchone()
        if not fa or not fb or not fa["family_id"] or not fb["family_id"]:
            raise HTTPException(status_code=404, detail="Families not found")
        rows_a = conn.execute(
            """
            SELECT isin, weight_pct FROM holdings_snapshot
            WHERE family_id = %s AND isin IS NOT NULL
            ORDER BY as_of DESC LIMIT 500
            """,
            (fa["family_id"],),
        ).fetchall()
        rows_b = conn.execute(
            """
            SELECT isin, weight_pct FROM holdings_snapshot
            WHERE family_id = %s AND isin IS NOT NULL
            ORDER BY as_of DESC LIMIT 500
            """,
            (fb["family_id"],),
        ).fetchall()
    map_a = {r["isin"]: float(r["weight_pct"] or 0) for r in rows_a}
    map_b = {r["isin"]: float(r["weight_pct"] or 0) for r in rows_b}
    shared = set(map_a) & set(map_b)
    overlap_pct = sum(min(map_a[i], map_b[i]) for i in shared)
    jaccard = len(shared) / len(set(map_a) | set(map_b)) if map_a or map_b else 0
    return {
        "code_a": code_a,
        "code_b": code_b,
        "weighted_overlap_pct": overlap_pct,
        "jaccard_isin": jaccard,
        "shared_holdings": len(shared),
    }
