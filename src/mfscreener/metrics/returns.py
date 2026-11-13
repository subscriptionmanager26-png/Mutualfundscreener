from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, timedelta

HORIZONS: dict[str, timedelta | None] = {
    "1w": timedelta(days=7),
    "1m": timedelta(days=30),
    "3m": timedelta(days=91),
    "6m": timedelta(days=182),
    "ytd": None,
    "1y": timedelta(days=365),
    "2y": timedelta(days=365 * 2),
    "3y": timedelta(days=365 * 3),
    "5y": timedelta(days=365 * 5),
    "10y": timedelta(days=365 * 10),
    "si": None,
}

ANNUALIZED = {"1y", "2y", "3y", "5y", "10y"}

# A start NAV older than this relative to the horizon start means the scheme has a data gap there.
MAX_START_GAP = timedelta(days=10)


def _nav_on_or_before(
    dates: Sequence[date],
    navs: Sequence[float],
    target: date,
) -> tuple[date, float] | None:
    i = bisect_right(dates, target) - 1
    if i < 0:
        return None
    return dates[i], float(navs[i])


def trailing_return(
    dates: Sequence[date],
    navs: Sequence[float],
    as_of: date,
    horizon_key: str,
) -> float | None:
    """Simple return under 1Y, CAGR for 1Y and longer; None when history does not cover the window."""
    if len(dates) < 2:
        return None
    end = _nav_on_or_before(dates, navs, as_of)
    if end is None or end[1] <= 0:
        return None
    end_nav = end[1]

    if horizon_key == "si":
        start_date, start_nav = dates[0], float(navs[0])
        if start_nav <= 0:
            return None
        years = (as_of - start_date).days / 365.0
        ratio = end_nav / start_nav
        return float(ratio ** (1 / years) - 1) if years >= 1 else float(ratio - 1)

    if horizon_key == "ytd":
        # Last NAV of the previous year is the base.
        target = date(as_of.year, 1, 1) - timedelta(days=1)
    else:
        delta = HORIZONS.get(horizon_key)
        if delta is None:
            return None
        target = as_of - delta

    start = _nav_on_or_before(dates, navs, target)
    if start is None or start[1] <= 0 or target - start[0] > MAX_START_GAP:
        return None
    ratio = end_nav / start[1]
    if horizon_key in ANNUALIZED:
        years = HORIZONS[horizon_key].days / 365.0  # type: ignore[union-attr]
        return float(ratio ** (1 / years) - 1)
    return float(ratio - 1)


def day_change_pct(dates: Sequence[date], navs: Sequence[float]) -> float | None:
    if len(navs) < 2:
        return None
    a, b = float(navs[-2]), float(navs[-1])
    if a <= 0:
        return None
    return float((b - a) / a * 100)
