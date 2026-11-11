from __future__ import annotations

import json
from datetime import date

from mfscreener.db import connection
from mfscreener.metrics.returns import HORIZONS, day_change_pct, trailing_return


def compute_returns_and_screener() -> dict[str, int]:
    updated = 0
    with connection() as conn:
        schemes = conn.execute(
            """
            SELECT scheme_code, amc, scheme_name, sebi_category, plan, option, family_id,
                   last_nav, last_nav_date
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
            rets = {h: trailing_return(dates, navs, as_of, h) for h in HORIZONS}
            dchg = day_change_pct(dates, navs)
            ter_row = conn.execute(
                """
                SELECT ter_pct FROM ter_daily
                WHERE scheme_code = %s AND plan = %s
                ORDER BY ter_date DESC LIMIT 1
                """,
                (code, scheme["plan"]),
            ).fetchone()
            ter = float(ter_row["ter_pct"]) if ter_row and ter_row["ter_pct"] is not None else None
            conn.execute(
                """
                INSERT INTO screener_wide (
                    scheme_code, as_of_date, amc, scheme_name, sebi_category, plan, option,
                    family_id, last_nav, day_change_pct, ter_pct,
                    ret_1w, ret_1m, ret_3m, ret_6m, ret_ytd, ret_1y, ret_3y, ret_5y, ret_si,
                    updated_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()
                )
                ON CONFLICT (scheme_code) DO UPDATE SET
                    as_of_date = EXCLUDED.as_of_date,
                    last_nav = EXCLUDED.last_nav,
                    day_change_pct = EXCLUDED.day_change_pct,
                    ter_pct = COALESCE(EXCLUDED.ter_pct, screener_wide.ter_pct),
                    ret_1w = EXCLUDED.ret_1w,
                    ret_1m = EXCLUDED.ret_1m,
                    ret_3m = EXCLUDED.ret_3m,
                    ret_6m = EXCLUDED.ret_6m,
                    ret_ytd = EXCLUDED.ret_ytd,
                    ret_1y = EXCLUDED.ret_1y,
                    ret_3y = EXCLUDED.ret_3y,
                    ret_5y = EXCLUDED.ret_5y,
                    ret_si = EXCLUDED.ret_si,
                    updated_at = NOW()
                """,
                (
                    code,
                    as_of,
                    scheme["amc"],
                    scheme["scheme_name"],
                    scheme["sebi_category"],
                    scheme["plan"],
                    scheme["option"],
                    scheme["family_id"],
                    scheme["last_nav"],
                    dchg,
                    ter,
                    rets.get("1w"),
                    rets.get("1m"),
                    rets.get("3m"),
                    rets.get("6m"),
                    rets.get("ytd"),
                    rets.get("1y"),
                    rets.get("3y"),
                    rets.get("5y"),
                    rets.get("si"),
                ),
            )
            updated += 1
        conn.commit()
    return {"schemes_updated": updated}
