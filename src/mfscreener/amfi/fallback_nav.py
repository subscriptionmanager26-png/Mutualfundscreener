from __future__ import annotations

import time
from datetime import date, datetime

import httpx

from mfscreener.config import get_settings


class ExternalNavError(Exception):
    pass


def fetch_nav_mfapi(scheme_code: int) -> list[tuple[date, float]]:
    """Full NAV series from mfapi.in (AMFI mirror)."""
    settings = get_settings()
    url = f"{settings.mfapi_base_url}/mf/{scheme_code}"
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for attempt in range(4):
            try:
                resp = client.get(url)
                if resp.status_code == 429:
                    time.sleep(2**attempt + 1)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                break
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise ExternalNavError(f"mfapi {scheme_code}: {exc}") from exc
                time.sleep(2**attempt + 1)
        else:
            raise ExternalNavError(f"mfapi {scheme_code}: throttled")
    if payload.get("status") != "SUCCESS":
        raise ExternalNavError(f"mfapi {scheme_code}: {payload.get('status')}")
    out: list[tuple[date, float]] = []
    for row in payload.get("data") or []:
        try:
            nav = float(row["nav"])
            nav_date = datetime.strptime(row["date"], "%d-%m-%Y").date()
        except (KeyError, ValueError, TypeError):
            continue
        if nav > 0:
            out.append((nav_date, nav))
    return out


def fetch_nav_finapi(
    scheme_code: int,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[date, float]]:
    """NAV series from finapi.upvaly.com (optional API key for higher limits)."""
    settings = get_settings()
    start = start or date(2006, 4, 1)
    end = end or date.today()
    url = (
        f"{settings.finapi_base_url}/api/mf/scheme-code/{scheme_code}/nav"
        f"?startDate={start.isoformat()}&endDate={end.isoformat()}"
    )
    headers: dict[str, str] = {"User-Agent": "mf-screener/0.1"}
    if settings.finapi_api_key:
        headers["X-API-Key"] = settings.finapi_api_key
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for attempt in range(4):
            try:
                resp = client.get(url, headers=headers)
                if resp.status_code == 429:
                    time.sleep(2**attempt + 2)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                break
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise ExternalNavError(f"finapi {scheme_code}: {exc}") from exc
                time.sleep(2**attempt + 2)
        else:
            raise ExternalNavError(f"finapi {scheme_code}: throttled")
    if payload.get("status") != "success":
        raise ExternalNavError(f"finapi {scheme_code}: {payload.get('message')}")
    data = payload.get("data") or {}
    history = data.get("navHistory") or data.get("nav_history") or []
    out: list[tuple[date, float]] = []
    for row in history:
        try:
            nav = float(row["nav"])
            raw = row.get("navDate") or row.get("date")
            nav_date = date.fromisoformat(str(raw)[:10])
        except (KeyError, ValueError, TypeError):
            continue
        if nav > 0:
            out.append((nav_date, nav))
    return out


def fetch_nav_with_fallback(scheme_code: int) -> tuple[list[tuple[date, float]], str]:
    try:
        return fetch_nav_mfapi(scheme_code), "mfapi"
    except ExternalNavError:
        return fetch_nav_finapi(scheme_code), "finapi"
