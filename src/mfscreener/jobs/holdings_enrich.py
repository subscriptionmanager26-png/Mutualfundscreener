from __future__ import annotations

from datetime import date

import httpx

from mfscreener.config import get_settings
from mfscreener.db import connection


def _mfdata_json(base_url: str, path: str) -> dict | None:
    url = f"{base_url}{path}"
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    if payload.get("status") != "success":
        return None
    return payload.get("data")


def sync_holdings_for_active_families(limit: int = 50) -> dict[str, int]:
    """Pull holdings from mfdata.in via AMFI scheme_code → mfdata family_id (best-effort)."""
    settings = get_settings()
    families_updated = 0
    rows_upserted = 0
    with connection() as conn:
        schemes = conn.execute(
            """
            SELECT scheme_code, family_id FROM scheme_master
            WHERE is_active AND plan = 'direct' AND option = 'growth'
            ORDER BY last_nav_date DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        for row in schemes:
            scheme_code = int(row["scheme_code"])
            family_id = int(row["family_id"])
            meta = _mfdata_json(settings.mfdata_base_url, f"/api/v1/schemes/{scheme_code}")
            if not meta:
                continue
            ext_family = meta.get("family_id")
            if not ext_family:
                continue
            data = _mfdata_json(
                settings.mfdata_base_url,
                f"/api/v1/families/{ext_family}/holdings",
            )
            if data is None:
                continue
            as_of_raw = data.get("as_of") or data.get("date")
            if not as_of_raw:
                continue
            if isinstance(as_of_raw, str) and len(as_of_raw) == 7:
                as_of = date(int(as_of_raw[:4]), int(as_of_raw[5:7]), 1)
            else:
                try:
                    as_of = date.fromisoformat(str(as_of_raw)[:10])
                except ValueError:
                    as_of = date.today().replace(day=1)
            holdings = data.get("holdings") or data.get("portfolio") or []
            weights: list[float] = []
            equity_weight = 0.0
            for h in holdings:
                w = h.get("weight_percent") or h.get("weight_pct") or h.get("weight")
                try:
                    w_f = float(w) if w is not None else 0.0
                except (TypeError, ValueError):
                    w_f = 0.0
                weights.append(w_f)
                asset = (h.get("asset_class") or h.get("type") or "").lower()
                if "equity" in asset or "stock" in asset:
                    equity_weight += w_f
                key = str(h.get("isin") or h.get("name") or h.get("instrument") or len(weights))
                conn.execute(
                    """
                    INSERT INTO holdings_snapshot (
                        family_id, as_of, holding_key, asset_class, instrument_name,
                        isin, weight_pct, sector, credit_rating, source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'mfdata')
                    ON CONFLICT (family_id, as_of, holding_key) DO UPDATE SET
                        weight_pct = EXCLUDED.weight_pct,
                        ingested_at = NOW()
                    """,
                    (
                        family_id,
                        as_of,
                        key,
                        h.get("asset_class") or h.get("type"),
                        h.get("name") or h.get("instrument"),
                        h.get("isin"),
                        w_f,
                        h.get("sector"),
                        h.get("rating") or h.get("credit_rating"),
                    ),
                )
                rows_upserted += 1
            weights.sort(reverse=True)
            top10 = sum(weights[:10]) if weights else None
            if top10 is not None:
                conn.execute(
                    """
                    INSERT INTO portfolio_metrics_snapshot (family_id, as_of, metric, value)
                    VALUES (%s, %s, 'top10_concentration_pct', %s)
                    ON CONFLICT (family_id, as_of, metric) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (family_id, as_of, top10),
                )
                conn.execute(
                    """
                    INSERT INTO portfolio_metrics_snapshot (family_id, as_of, metric, value)
                    VALUES (%s, %s, 'equity_pct', %s)
                    ON CONFLICT (family_id, as_of, metric) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (family_id, as_of, equity_weight),
                )
            conn.execute(
                """
                UPDATE screener_wide SET
                    has_holdings = TRUE,
                    top10_concentration_pct = %s,
                    equity_pct = %s,
                    updated_at = NOW()
                WHERE family_id = %s
                """,
                (top10, equity_weight or None, family_id),
            )
            families_updated += 1
        conn.commit()
    return {"families": families_updated, "holding_rows": rows_upserted}
