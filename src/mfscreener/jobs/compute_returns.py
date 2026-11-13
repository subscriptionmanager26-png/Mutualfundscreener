from __future__ import annotations

from mfscreener.db import connection
from mfscreener.metrics.returns import HORIZONS, day_change_pct, trailing_return

RETURN_COLUMNS = [f"ret_{h}" for h in HORIZONS]

# SQL twin of mfscreener.amfi.ter.normalize_fund_name; both sides of the TER join must agree.
NORMALIZE_SQL = "btrim(regexp_replace(lower({col}), '[^a-z0-9]+', ' ', 'g'))"


def compute_returns_and_screener() -> dict[str, int]:
    updated = 0
    columns = [
        "scheme_code", "as_of_date", "amc", "scheme_name", "sebi_category", "plan", "option",
        "family_id", "last_nav", "day_change_pct", "nav_days", "first_nav_date", *RETURN_COLUMNS,
    ]
    placeholders = ",".join(["%s"] * len(columns))
    updates = ",".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "scheme_code")
    upsert_sql = f"""
        INSERT INTO screener_wide ({",".join(columns)}, updated_at)
        VALUES ({placeholders}, NOW())
        ON CONFLICT (scheme_code) DO UPDATE SET {updates}, updated_at = NOW()
    """
    with connection() as conn:
        conn.execute(
            """
            DELETE FROM screener_wide sw USING scheme_master sm
            WHERE sm.scheme_code = sw.scheme_code AND NOT sm.is_active
            """
        )
        schemes = conn.execute(
            """
            SELECT scheme_code, amc, scheme_name, sebi_category, plan, option, family_id
            FROM scheme_master
            WHERE is_active AND last_nav IS NOT NULL AND last_nav > 0
            """
        ).fetchall()
        for scheme in schemes:
            code = scheme["scheme_code"]
            nav_rows = conn.execute(
                """
                SELECT nav_date, nav FROM nav_daily
                WHERE scheme_code = %s AND nav > 0
                ORDER BY nav_date
                """,
                (code,),
            ).fetchall()
            if not nav_rows:
                continue
            dates = [r["nav_date"] for r in nav_rows]
            navs = [float(r["nav"]) for r in nav_rows]
            as_of = dates[-1]
            rets = [trailing_return(dates, navs, as_of, h) for h in HORIZONS]
            conn.execute(
                upsert_sql,
                (
                    code,
                    as_of,
                    scheme["amc"],
                    scheme["scheme_name"],
                    scheme["sebi_category"],
                    scheme["plan"],
                    scheme["option"],
                    scheme["family_id"],
                    navs[-1],
                    day_change_pct(dates, navs),
                    len(dates),
                    dates[0],
                    *rets,
                ),
            )
            updated += 1
        ter_matched = apply_latest_ter(conn)
        conn.commit()
    return {"schemes_updated": updated, "ter_matched": ter_matched}


def apply_latest_ter(conn) -> int:
    cur = conn.execute(
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (fund_name_norm)
                fund_name_norm, ter_regular_pct, ter_direct_pct
            FROM ter_fund_daily
            ORDER BY fund_name_norm, ter_date DESC
        )
        UPDATE screener_wide sw SET ter_pct = CASE
                WHEN sw.plan = 'direct' THEN l.ter_direct_pct
                WHEN sw.plan = 'regular' THEN l.ter_regular_pct
            END
        FROM latest l
        WHERE {NORMALIZE_SQL.format(col="sw.scheme_name")} = l.fund_name_norm
          AND sw.plan IN ('direct', 'regular')
        """
    )
    return cur.rowcount
