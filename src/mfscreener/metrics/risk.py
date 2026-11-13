from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np

TRADING_DAYS = 252
# A window counts as covered only if the series starts within this many days of the window start.
MAX_WINDOW_START_GAP = timedelta(days=10)


def window(
    dates: Sequence[date],
    values: Sequence[float],
    as_of: date,
    years: float,
) -> tuple[list[date], np.ndarray] | None:
    start = as_of - timedelta(days=round(365 * years))
    idx = [i for i, d in enumerate(dates) if d >= start]
    if len(idx) < 20:
        return None
    # Include the last point before the window so the first return spans the window start.
    first = max(idx[0] - 1, 0)
    if dates[first] > start + MAX_WINDOW_START_GAP or dates[first] < start - MAX_WINDOW_START_GAP:
        return None
    return list(dates[first:]), np.asarray(values[first:], dtype=float)


def daily_returns(values: np.ndarray) -> np.ndarray:
    if values.size < 2:
        return np.array([])
    return values[1:] / values[:-1] - 1


def cagr(values: np.ndarray, years: float) -> float | None:
    if values.size < 2 or values[0] <= 0 or years <= 0:
        return None
    return float((values[-1] / values[0]) ** (1 / years) - 1)


def annualized_vol(rets: np.ndarray) -> float | None:
    if rets.size < 20:
        return None
    return float(np.std(rets, ddof=1) * np.sqrt(TRADING_DAYS))


def downside_deviation(rets: np.ndarray, risk_free_annual: float) -> float | None:
    if rets.size < 20:
        return None
    shortfall = np.minimum(rets - risk_free_annual / TRADING_DAYS, 0.0)
    return float(np.sqrt(np.mean(shortfall**2)) * np.sqrt(TRADING_DAYS))


def max_drawdown(values: Sequence[float] | np.ndarray) -> float | None:
    arr = np.asarray(values, dtype=float)
    if arr.size < 2:
        return None
    peaks = np.maximum.accumulate(arr)
    return float(np.min(arr / peaks - 1))


def sharpe(annual_return: float | None, vol: float | None, risk_free_annual: float) -> float | None:
    if annual_return is None or not vol:
        return None
    return (annual_return - risk_free_annual) / vol


def sortino(
    annual_return: float | None,
    downside: float | None,
    risk_free_annual: float,
) -> float | None:
    if annual_return is None or not downside:
        return None
    return (annual_return - risk_free_annual) / downside


def beta_alpha(
    fund_dates: Sequence[date],
    fund_values: np.ndarray,
    bench: dict[date, float],
    years: float,
    risk_free_annual: float,
) -> tuple[float | None, float | None]:
    """Beta from daily returns on common dates; Jensen alpha from window CAGRs."""
    common = [(d, v) for d, v in zip(fund_dates, fund_values, strict=False) if d in bench]
    if len(common) < 0.8 * TRADING_DAYS * years:
        return None, None
    f = np.array([v for _, v in common], dtype=float)
    b = np.array([bench[d] for d, _ in common], dtype=float)
    fr, br = daily_returns(f), daily_returns(b)
    var_b = float(np.var(br, ddof=1))
    if var_b <= 0:
        return None, None
    beta = float(np.cov(fr, br, ddof=1)[0, 1] / var_b)
    fund_cagr, bench_cagr = cagr(f, years), cagr(b, years)
    if fund_cagr is None or bench_cagr is None:
        return beta, None
    alpha = fund_cagr - (risk_free_annual + beta * (bench_cagr - risk_free_annual))
    return beta, float(alpha)
