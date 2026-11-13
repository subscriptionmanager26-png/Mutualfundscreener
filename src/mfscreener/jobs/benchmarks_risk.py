from __future__ import annotations

from datetime import date, timedelta

import httpx

from mfscreener.config import get_settings
from mfscreener.db import connection
from mfscreener.metrics import risk

NSE_INDEX_CSV = "https://archives.nseindia.com/content/indices/ind_close_all_{date}.csv"

_NSE_SYMBOLS = {
    "NIFTY50": "NIFTY 50",
    "NIFTY100": "NIFTY 100",
    "NIFTY500": "NIFTY 500",
    "NIFTY_MIDCAP_150": "NIFTY MIDCAP 150",
    "NIFTY_SMALLCAP_250": "NIFTY SMALLCAP 250",
}

# NSE archives are often unreachable from cloud IPs, so each index is backed by the longest-running
# direct-growth index fund that tracks it. Its NAV is a total-return proxy, net of a small TER.
_PROXY_PATTERNS = {
    "NIFTY50": "%nifty 50 index%",
    "NIFTY100": "%nifty 100 index%",
    "NIFTY500": "%nifty 500 index%",
    "NIFTY_MIDCAP_150": "%midcap 150 index%",
    "NIFTY_SMALLCAP_250": "%smallcap 250 index%",
}
_PROXY_EXCLUDE = (
    "%next%", "%equal%", "%momentum%", "%quality%", "%value%", "%alpha%", "%low vol%",
    "%multicap%", "%etf%", "%fof%",
)

MIN_BENCHMARK_POINTS = 500
EQUITY_FALLBACK_BENCHMARKS = ("NIFTY500", "NIFTY50")


def _download_nse_close(trade_date: date) -> dict[str, float]:
    url = NSE_INDEX_CSV.format(date=trade_date.strftime("%d%m%Y"))
    headers = {"User-Agent": "Mozilla/5.0 (compatible; mf-screener/0.1)"}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
    except httpx.HTTPError:
        return {}
    if resp.status_code != 200:
        return {}
    out: dict[str, float] = {}
    for line in resp.text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        try:
            out[parts[0].strip().upper()] = float(parts[5])
        except ValueError:
            continue
    return out


def _proxy_scheme(conn, pattern: str) -> int | None:
    exclude_sql = " AND ".join("sm.scheme_name NOT ILIKE %s" for _ in _PROXY_EXCLUDE)
    row = conn.execute(
        f"""
        SELECT sm.scheme_code
        FROM scheme_master sm
        JOIN LATERAL (
            SELECT COUNT(*) AS n FROM nav_daily nd WHERE nd.scheme_code = sm.scheme_code
        ) c ON TRUE
        WHERE sm.is_active AND sm.plan = 'direct' AND sm.option = 'growth'
          AND sm.scheme_name ILIKE %s AND {exclude_sql}
        ORDER BY c.n DESC
        LIMIT 1
        """,
        (pattern, *_PROXY_EXCLUDE),
    ).fetchone()
    return int(row["scheme_code"]) if row else None


def sync_benchmarks(days: int = 5) -> dict[str, object]:
    stats: dict[str, object] = {"nse_levels": 0, "proxies": {}}
    with connection() as conn:
        for i in range(days):
            d = date.today() - timedelta(days=i)
            closes = _download_nse_close(d)
            for bench_id, symbol in _NSE_SYMBOLS.items():
                if symbol in closes:
                    cur = conn.execute(
                        """
                        INSERT INTO benchmark_levels (benchmark_id, level_date, close_value, source)
                        VALUES (%s, %s, %s, 'nse')
                        ON CONFLICT DO NOTHING
                        """,
                        (bench_id, d, closes[symbol]),
                    )
                    stats["nse_levels"] += cur.rowcount  # type: ignore[operator]

        for bench_id, pattern in _PROXY_PATTERNS.items():
            code = _proxy_scheme(conn, pattern)
            if code is None:
                continue
            # Proxy and NSE closes are on different scales, so a benchmark uses one source only.
            conn.execute(
                "DELETE FROM benchmark_levels WHERE benchmark_id = %s AND source <> %s",
                (bench_id, f"index-fund-proxy:{code}"),
            )
            cur = conn.execute(
                """
                INSERT INTO benchmark_levels (benchmark_id, level_date, close_value, source)
                SELECT %s, nav_date, nav, %s FROM nav_daily
                WHERE scheme_code = %s AND nav > 0
                ON CONFLICT DO NOTHING
                """,
                (bench_id, f"index-fund-proxy:{code}", code),
            )
            stats["proxies"][bench_id] = {"scheme_code": code, "inserted": cur.rowcount}  # type: ignore[index]
        conn.commit()
    return stats


def _load_benchmarks(conn) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for row in conn.execute("SELECT benchmark_id, level_date, close_value FROM benchmark_levels"):
        out.setdefault(row["benchmark_id"], {})[row["level_date"]] = float(row["close_value"])
    return {k: v for k, v in out.items() if len(v) >= MIN_BENCHMARK_POINTS}


def _pick_benchmark(
    category: str | None,
    mapped: str | None,
    available: dict[str, dict[date, float]],
) -> str | None:
    if mapped in available:
        return mapped
    if category and category.startswith("Equity Scheme"):
        return next((b for b in EQUITY_FALLBACK_BENCHMARKS if b in available), None)
    return None


def compute_risk_and_ranks() -> dict[str, int]:
    rf = get_settings().risk_free_annual
    updated = 0
    with connection() as conn:
        benchmarks = _load_benchmarks(conn)
        schemes = conn.execute(
            """
            SELECT sw.scheme_code, sw.as_of_date, sw.sebi_category,
                   COALESCE(sm.benchmark_id, m.benchmark_id) AS mapped_benchmark
            FROM screener_wide sw
            JOIN scheme_master sm ON sm.scheme_code = sw.scheme_code
            LEFT JOIN category_benchmark_map m ON m.sebi_category = sw.sebi_category
            """
        ).fetchall()
        for s in schemes:
            as_of: date = s["as_of_date"]
            rows = conn.execute(
                """
                SELECT nav_date, nav FROM nav_daily
                WHERE scheme_code = %s AND nav > 0 AND nav_date >= %s
                ORDER BY nav_date
                """,
                (s["scheme_code"], as_of - timedelta(days=365 * 5 + 15)),
            ).fetchall()
            if len(rows) < 20:
                continue
            dates = [r["nav_date"] for r in rows]
            navs = [float(r["nav"]) for r in rows]

            m: dict[str, float | None] = {}
            for label, years in (("1y", 1), ("3y", 3), ("5y", 5)):
                w = risk.window(dates, navs, as_of, years)
                if w is None:
                    m[f"mdd_{label}"] = None
                    if label != "5y":
                        m[f"vol_{label}"] = m[f"sharpe_{label}"] = None
                    continue
                w_vals = w[1]
                rets = risk.daily_returns(w_vals)
                annual = risk.cagr(w_vals, years)
                m[f"mdd_{label}"] = risk.max_drawdown(w_vals)
                if label == "5y":
                    continue
                vol = risk.annualized_vol(rets)
                m[f"vol_{label}"] = vol
                m[f"sharpe_{label}"] = risk.sharpe(annual, vol, rf)
                if label == "3y":
                    m["sortino_3y"] = risk.sortino(annual, risk.downside_deviation(rets, rf), rf)

            bench_id = _pick_benchmark(s["sebi_category"], s["mapped_benchmark"], benchmarks)
            beta = alpha = None
            w3 = risk.window(dates, navs, as_of, 3)
            if bench_id and w3 is not None:
                beta, alpha = risk.beta_alpha(w3[0], w3[1], benchmarks[bench_id], 3, rf)

            conn.execute(
                """
                UPDATE screener_wide SET
                    vol_1y = %s, vol_3y = %s, sharpe_1y = %s, sharpe_3y = %s, sortino_3y = %s,
                    mdd_1y = %s, mdd_3y = %s, mdd_5y = %s,
                    beta_3y = %s, alpha_3y = %s, benchmark_id = %s, updated_at = NOW()
                WHERE scheme_code = %s
                """,
                (
                    m.get("vol_1y"), m.get("vol_3y"), m.get("sharpe_1y"), m.get("sharpe_3y"),
                    m.get("sortino_3y"), m.get("mdd_1y"), m.get("mdd_3y"), m.get("mdd_5y"),
                    beta, alpha, bench_id if beta is not None else None, s["scheme_code"],
                ),
            )
            updated += 1

        compute_category_ranks(conn)
        conn.commit()
    return {"risk_updated": updated, "benchmarks_available": len(benchmarks)}


def compute_category_ranks(conn) -> None:
    """Percentile within SEBI category, plan and option: 100 is the best fund in its peer group."""
    for metric, col in (
        ("ret_1y", "rank_ret_1y_category"),
        ("ret_3y", "rank_ret_3y_category"),
        ("sharpe_3y", "rank_sharpe_3y_category"),
    ):
        conn.execute(f"UPDATE screener_wide SET {col} = NULL")
        conn.execute(
            f"""
            UPDATE screener_wide sw SET {col} = r.pct
            FROM (
                SELECT scheme_code,
                    100 * PERCENT_RANK() OVER (
                        PARTITION BY sebi_category, plan, option ORDER BY {metric}
                    ) AS pct
                FROM screener_wide
                WHERE {metric} IS NOT NULL AND sebi_category IS NOT NULL
            ) r
            WHERE sw.scheme_code = r.scheme_code
            """
        )
