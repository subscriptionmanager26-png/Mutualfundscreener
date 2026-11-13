from datetime import date, timedelta

import numpy as np
import pytest

from mfscreener.amfi.ter import normalize_fund_name
from mfscreener.metrics import risk
from mfscreener.metrics.returns import trailing_return


def _daily_series(start: date, days: int, daily_growth: float) -> tuple[list[date], list[float]]:
    dates = [start + timedelta(days=i) for i in range(days)]
    navs = [100 * (1 + daily_growth) ** i for i in range(days)]
    return dates, navs


def test_cagr_horizons_match_constant_growth():
    dates, navs = _daily_series(date(2015, 1, 1), 365 * 6, 0.0003)
    as_of = dates[-1]
    expected = (1.0003**365) - 1
    assert trailing_return(dates, navs, as_of, "1y") == pytest.approx(expected, rel=1e-6)
    assert trailing_return(dates, navs, as_of, "3y") == pytest.approx(expected, rel=1e-6)
    assert trailing_return(dates, navs, as_of, "5y") == pytest.approx(expected, rel=1e-6)


def test_horizon_longer_than_history_is_none():
    dates, navs = _daily_series(date(2024, 1, 1), 400, 0.0003)
    assert trailing_return(dates, navs, dates[-1], "3y") is None
    assert trailing_return(dates, navs, dates[-1], "1y") is not None


def test_gap_at_window_start_is_none():
    dates, navs = _daily_series(date(2020, 1, 1), 365 * 3, 0.0003)
    # Drop the year before the 1Y start so the nearest older NAV is far outside the window.
    cutoff_hi = dates[-1] - timedelta(days=360)
    cutoff_lo = dates[-1] - timedelta(days=700)
    kept = [(d, n) for d, n in zip(dates, navs) if not (cutoff_lo < d < cutoff_hi)]
    d2, n2 = [d for d, _ in kept], [n for _, n in kept]
    assert trailing_return(d2, n2, d2[-1], "1y") is None


def test_max_drawdown():
    assert risk.max_drawdown([100, 120, 90, 130]) == pytest.approx(-0.25)


def test_beta_of_fund_against_itself_is_one():
    dates, navs = _daily_series(date(2020, 1, 1), 365 * 4, 0.0004)
    rng = np.random.default_rng(1)
    noisy = list(np.array(navs) * np.cumprod(1 + rng.normal(0, 0.01, len(navs))))
    w = risk.window(dates, noisy, dates[-1], 3)
    assert w is not None
    bench = dict(zip(dates, noisy))
    beta, alpha = risk.beta_alpha(w[0], w[1], bench, 3, 0.065)
    assert beta == pytest.approx(1.0)
    assert alpha == pytest.approx(0.0, abs=1e-9)


def test_normalize_fund_name_matches_sql_rule():
    assert normalize_fund_name("360 ONE Balanced-Hybrid  Fund ") == "360 one balanced hybrid fund"
