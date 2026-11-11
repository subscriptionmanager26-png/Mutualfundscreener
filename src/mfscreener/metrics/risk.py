from __future__ import annotations

from typing import Sequence

import numpy as np


def _daily_returns(values: Sequence[float]) -> np.ndarray:
    arr = np.array(values, dtype=float)
    if arr.size < 2:
        return np.array([])
    return np.diff(arr) / arr[:-1]


def annualized_vol(daily_rets: np.ndarray) -> float | None:
    if daily_rets.size < 5:
        return None
    return float(np.std(daily_rets, ddof=1) * np.sqrt(252))


def max_drawdown(values: Sequence[float]) -> float | None:
    arr = np.array(values, dtype=float)
    if arr.size < 2:
        return None
    peak = arr[0]
    mdd = 0.0
    for v in arr:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak > 0 else 0
        mdd = min(mdd, dd)
    return float(mdd)


def beta_alpha_sharpe(
    fund_navs: Sequence[float],
    bench_navs: Sequence[float],
    *,
    risk_free_annual: float,
) -> tuple[float | None, float | None, float | None]:
    if len(fund_navs) != len(bench_navs) or len(fund_navs) < 30:
        return None, None, None
    rf_daily = risk_free_annual / 252
    fr = _daily_returns(fund_navs)
    br = _daily_returns(bench_navs)
    n = min(fr.size, br.size)
    if n < 20:
        return None, None, None
    fr, br = fr[-n:], br[-n:]
    cov = np.cov(fr, br)
    var_b = cov[1, 1]
    if var_b <= 0:
        return None, None, None
    beta = float(cov[0, 1] / var_b)
    fund_ann = float(np.mean(fr) * 252)
    bench_ann = float(np.mean(br) * 252)
    alpha = fund_ann - risk_free_annual - beta * (bench_ann - risk_free_annual)
    vol = float(np.std(fr, ddof=1) * np.sqrt(252))
    sharpe = (fund_ann - risk_free_annual) / vol if vol > 0 else None
    return beta, alpha, sharpe
