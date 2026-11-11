from __future__ import annotations

from datetime import date, timedelta

import httpx
import numpy as np

from mfscreener.config import get_settings
from mfscreener.db import connection
from mfscreener.metrics.risk import annualized_vol, beta_alpha_sharpe, max_drawdown

# NSE public index CSV archive (symbol → numeric index on archive).
NSE_INDEX_CSV = "https://archives.nseindia.com/content/indices/ind_close_all_{date}.csv"


def _download_nse_close(trade_date: date) -> dict[str, float]:
    """Best-effort NSE index closes; returns {index_name: close}."""
    url = NSE_INDEX_CSV.format(date=trade_date.strftime("%d%m%Y"))
    headers = {"User-Agent": "Mozilla/5.0 (compatible; mf-screener/0.1)"}
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return {}
    except httpx.HTTPError:
        return {}
    out: dict[str, float] = {}
    for line in resp.text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 3:
            continue
        name, _, close = parts[0].strip(), parts[1], parts[2].strip()
        try:
            out[name] = float(close)
        except ValueError:
            continue
    return out


_SYMBOL_MAP = {
    "NIFTY50": "NIFTY 50",
    "NIFTY100": "NIFTY 100",
    "NIFTY500": "NIFTY 500",
    "NIFTY_MIDCAP_150": "NIFTY MIDCAP 150",
    "NIFTY_SMALLCAP_250": "NIFTY SMALLCAP 250",
}


def seed_benchmark_from_scheme(conn, benchmark_id: str, scheme_code: int) -> int:
    """Fallback: use a liquid index fund's NAV as benchmark proxy when NSE CSV is blocked."""
    rows = conn.execute(
        """
        SELECT nav_date, nav FROM nav_daily
        WHERE scheme_code = %s AND nav > 0
        ORDER BY nav_date
        """,
        (scheme_code,),
    ).fetchall()
    n = 0
    for r in rows:
        cur = conn.execute(
            """
            INSERT INTO benchmark_levels (benchmark_id, level_date, close_value, source)
            VALUES (%s, %s, %s, 'index-fund-proxy')
            ON CONFLICT DO NOTHING
            """,
            (benchmark_id, r["nav_date"], r["nav"]),
        )
        if cur.rowcount:
            n += 1
    return n


def sync_benchmarks(days: int = 5) -> dict[str, int]:
    inserted = 0
    today = date.today()
    with connection() as conn:
        for i in range(days):
            d = today - timedelta(days=i)
            closes = _download_nse_close(d)
            if not closes:
                continue
            for bench_id, symbol in _SYMBOL_MAP.items():
                close = closes.get(symbol)
                if close is None:
                    continue
                cur = conn.execute(
                    """
                    INSERT INTO benchmark_levels (benchmark_id, level_date, close_value)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (bench_id, d, close),
                )
                if cur.rowcount:
                    inserted += 1
        if inserted == 0:
            # UTI Nifty 50 Index Fund Direct Growth — common AMFI code
            inserted += seed_benchmark_from_scheme(conn, "NIFTY50", 120716)
            conn.execute(
                """
                UPDATE scheme_master SET benchmark_id = 'NIFTY50'
                WHERE scheme_code IN (
                    SELECT scheme_code FROM scheme_master
                    WHERE sebi_category ILIKE '%Large Cap%' LIMIT 2000
                )
                """
            )
        conn.commit()
    return {"levels_inserted": inserted}


def compute_risk_and_ranks() -> dict[str, int]:
    settings = get_settings()
    updated = 0
    with connection() as conn:
        schemes = conn.execute(
            """
            SELECT s.scheme_code, s.sebi_category, s.plan, s.family_id,
                   COALESCE(s.benchmark_id, m.benchmark_id) AS benchmark_id
            FROM scheme_master s
            LEFT JOIN category_benchmark_map m ON m.sebi_category = s.sebi_category
            WHERE s.is_active
            """
        ).fetchall()
        for scheme in schemes:
            code = scheme["scheme_code"]
            nav_rows = conn.execute(
                """
                SELECT nav_date, nav FROM nav_daily
                WHERE scheme_code = %s AND nav > 0
                ORDER BY nav_date DESC
                LIMIT 800
                """,
                (code,),
            ).fetchall()
            if len(nav_rows) < 20:
                continue
            nav_rows.reverse()
            navs = [float(r["nav"]) for r in nav_rows]
            daily = np.diff(np.array(navs)) / np.array(navs[:-1])
            vol_1y = annualized_vol(daily[-252:]) if daily.size >= 252 else annualized_vol(daily)
            mdd_1y = max_drawdown(navs[-252:]) if len(navs) >= 252 else max_drawdown(navs)
            mdd_3y = max_drawdown(navs[-756:]) if len(navs) >= 756 else max_drawdown(navs)
            beta = alpha = sharpe = None
            bench_id = scheme["benchmark_id"]
            if bench_id:
                bench_rows = conn.execute(
                    """
                    SELECT level_date, close_value FROM benchmark_levels
                    WHERE benchmark_id = %s
                    ORDER BY level_date DESC
                    LIMIT 800
                    """,
                    (bench_id,),
                ).fetchall()
                if len(bench_rows) >= 15:
                    bench_rows.reverse()
                    bench_navs = [float(r["close_value"]) for r in bench_rows]
                    # align by length tail
                    n = min(len(navs), len(bench_navs))
                    beta, alpha, sharpe = beta_alpha_sharpe(
                        navs[-n:],
                        bench_navs[-n:],
                        risk_free_annual=settings.risk_free_annual,
                    )
            conn.execute(
                """
                UPDATE screener_wide SET
                    vol_1y = %s,
                    mdd_1y = %s,
                    mdd_3y = %s,
                    beta_3y = %s,
                    alpha_3y = %s,
                    sharpe_3y = %s,
                    updated_at = NOW()
                WHERE scheme_code = %s
                """,
                (vol_1y, mdd_1y, mdd_3y, beta, alpha, sharpe, code),
            )
            updated += 1

        # Category percentile ranks (0-100) within sebi_category + plan
        for metric, col in (
            ("ret_1y", "rank_ret_1y_category"),
            ("ret_3y", "rank_ret_3y_category"),
            ("sharpe_3y", "rank_sharpe_3y_category"),
        ):
            rows = conn.execute(
                f"""
                SELECT scheme_code, {metric} AS v, sebi_category, plan
                FROM screener_wide
                WHERE {metric} IS NOT NULL AND sebi_category IS NOT NULL
                """
            ).fetchall()
            buckets: dict[tuple[str, str], list[tuple[int, float]]] = {}
            for r in rows:
                key = (r["sebi_category"] or "", r["plan"] or "")
                buckets.setdefault(key, []).append((r["scheme_code"], float(r["v"])))
            for _, items in buckets.items():
                items.sort(key=lambda x: x[1])
                n = len(items)
                for i, (scode, _) in enumerate(items):
                    pct = 100.0 * (i + 1) / n
                    conn.execute(
                        f"UPDATE screener_wide SET {col} = %s WHERE scheme_code = %s",
                        (pct, scode),
                    )
        conn.commit()
    return {"risk_updated": updated}
