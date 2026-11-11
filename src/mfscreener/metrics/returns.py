from __future__ import annotations

from datetime import date, timedelta
from typing import Sequence

import numpy as np


HORIZONS: dict[str, timedelta | None] = {
    "1w": timedelta(days=7),
    "1m": timedelta(days=30),
    "3m": timedelta(days=91),
    "6m": timedelta(days=182),
    "ytd": None,
    "1y": timedelta(days=365),
    "3y": timedelta(days=365 * 3),
    "5y": timedelta(days=365 * 5),
    "si": None,
}


def _nav_on_or_before(dates: Sequence[date], navs: Sequence[float], target: date) -> float | None:
    for d, n in zip(reversed(dates), reversed(navs), strict=False):
        if d <= target:
            return float(n)
    return None


def trailing_return(
    dates: Sequence[date],
    navs: Sequence[float],
    as_of: date,
    horizon_key: str,
) -> float | None:
    if len(dates) < 2:
        return None
    end_nav = _nav_on_or_before(dates, navs, as_of)
    if end_nav is None or end_nav <= 0:
        return None
    start_date: date | None
    if horizon_key == "ytd":
        start_date = date(as_of.year, 1, 1)
    elif horizon_key == "si":
        start_date = dates[0]
    else:
        delta = HORIZONS.get(horizon_key)
        if delta is None:
            return None
        start_date = as_of - delta
    start_nav = _nav_on_or_before(dates, navs, start_date)
    if start_nav is None or start_nav <= 0:
        return None
    ratio = end_nav / start_nav
    if horizon_key in ("1y", "3y", "5y", "si") and horizon_key != "si":
        years = HORIZONS[horizon_key].days / 365.0  # type: ignore[union-attr]
        return float(ratio ** (1 / years) - 1)
    if horizon_key == "si":
        years = max((as_of - dates[0]).days / 365.0, 1 / 365)
        if years >= 1:
            return float(ratio ** (1 / years) - 1)
        return float(ratio - 1)
    return float(ratio - 1)


def day_change_pct(dates: Sequence[date], navs: Sequence[float]) -> float | None:
    if len(navs) < 2:
        return None
    a, b = float(navs[-2]), float(navs[-1])
    if a <= 0:
        return None
    return float((b - a) / a * 100)
