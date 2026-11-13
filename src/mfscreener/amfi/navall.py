from __future__ import annotations

from datetime import date, timedelta

import httpx

from mfscreener.amfi.parser import NavAllRow, family_key, parse_navall
from mfscreener.config import get_settings
from mfscreener.db import connection
from mfscreener.storage import archive_raw


def download_navall() -> tuple[str, str]:
    settings = get_settings()
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        resp = client.get(settings.amfi_navall_url)
        resp.raise_for_status()
        text = resp.text
    key = archive_raw(
        source="amfi",
        filename="NAVAll.txt",
        body=text.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
    )
    return text, key


def _ensure_family(conn, row: NavAllRow) -> int:
    fkey = family_key(row.amc, row.scheme_name)
    existing = conn.execute(
        "SELECT family_id FROM scheme_family WHERE family_key = %s",
        (fkey,),
    ).fetchone()
    if existing:
        return int(existing["family_id"])
    inserted = conn.execute(
        """
        INSERT INTO scheme_family (family_key, amc, base_scheme_name, sebi_category)
        VALUES (%s, %s, %s, %s)
        RETURNING family_id
        """,
        (fkey, row.amc, row.scheme_name, row.sebi_category),
    ).fetchone()
    assert inserted is not None
    return int(inserted["family_id"])


def upsert_navall_rows(rows: list[NavAllRow], file_as_of: date | None) -> dict[str, int]:
    settings = get_settings()
    stats = {"schemes": 0, "nav_inserted": 0, "nav_updated": 0, "anomalies": 0}
    if not rows:
        return stats

    reference_date = file_as_of
    if reference_date is None:
        reference_date = max(r.nav_date.date() for r in rows)
    stale_cutoff = reference_date - timedelta(days=settings.active_nav_stale_days)

    with connection() as conn:
        for row in rows:
            if row.nav <= 0:
                conn.execute(
                    """
                    INSERT INTO nav_anomaly (scheme_code, nav_date, anomaly_type, detail)
                    VALUES (%s, %s, 'zero_nav', %s::jsonb)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row.scheme_code,
                        row.nav_date.date(),
                        '{"note":"nav<=0"}',
                    ),
                )
                stats["anomalies"] += 1
                continue

            family_id = _ensure_family(conn, row)
            conn.execute(
                """
                INSERT INTO scheme_master (
                    scheme_code, family_id, isin_growth, isin_reinvest, amc, scheme_name,
                    plan, option, sebi_category, category_raw, last_nav, last_nav_date, is_active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
                ON CONFLICT (scheme_code) DO UPDATE SET
                    family_id = EXCLUDED.family_id,
                    isin_growth = COALESCE(EXCLUDED.isin_growth, scheme_master.isin_growth),
                    isin_reinvest = COALESCE(EXCLUDED.isin_reinvest, scheme_master.isin_reinvest),
                    amc = EXCLUDED.amc,
                    scheme_name = EXCLUDED.scheme_name,
                    plan = EXCLUDED.plan,
                    option = EXCLUDED.option,
                    sebi_category = COALESCE(EXCLUDED.sebi_category, scheme_master.sebi_category),
                    category_raw = COALESCE(EXCLUDED.category_raw, scheme_master.category_raw),
                    last_nav = EXCLUDED.last_nav,
                    last_nav_date = EXCLUDED.last_nav_date,
                    last_seen_at = NOW(),
                    is_active = TRUE
                """,
                (
                    row.scheme_code,
                    family_id,
                    row.isin_growth,
                    row.isin_reinvest,
                    row.amc,
                    row.scheme_name,
                    row.plan,
                    row.option,
                    row.sebi_category,
                    row.category_raw,
                    row.nav,
                    row.nav_date.date(),
                ),
            )
            stats["schemes"] += 1

            prior = conn.execute(
                """
                SELECT nav, nav_date FROM nav_daily
                WHERE scheme_code = %s
                ORDER BY nav_date DESC LIMIT 1
                """,
                (row.scheme_code,),
            ).fetchone()
            insert_nav = True
            if prior and prior["nav_date"] == row.nav_date.date():
                if float(prior["nav"]) == row.nav:
                    insert_nav = False
                else:
                    conn.execute(
                        """
                        UPDATE nav_daily SET nav = %s, ingested_at = NOW(), source = 'amfi-daily-corrected'
                        WHERE scheme_code = %s AND nav_date = %s
                        """,
                        (row.nav, row.scheme_code, row.nav_date.date()),
                    )
                    stats["nav_updated"] += 1
                    insert_nav = False
            elif prior and float(prior["nav"]) > 0 and row.nav / float(prior["nav"]) > 2:
                conn.execute(
                    """
                    INSERT INTO nav_anomaly (scheme_code, nav_date, anomaly_type, detail)
                    VALUES (%s, %s, 'split_like_jump', %s::jsonb)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row.scheme_code,
                        row.nav_date.date(),
                        f'{{"prior_nav":{prior["nav"]},"new_nav":{row.nav}}}',
                    ),
                )
                stats["anomalies"] += 1

            if insert_nav and (not prior or prior["nav_date"] < row.nav_date.date()):
                cur = conn.execute(
                    """
                    INSERT INTO nav_daily (scheme_code, nav_date, nav, source)
                    VALUES (%s, %s, %s, 'amfi-daily')
                    ON CONFLICT DO NOTHING
                    """,
                    (row.scheme_code, row.nav_date.date(), row.nav),
                )
                if cur.rowcount:
                    stats["nav_inserted"] += 1
                conn.execute(
                    """
                    UPDATE scheme_master SET first_nav_date = LEAST(
                        COALESCE(first_nav_date, %s), %s
                    ) WHERE scheme_code = %s
                    """,
                    (row.nav_date.date(), row.nav_date.date(), row.scheme_code),
                )

        conn.execute(
            """
            UPDATE scheme_master
            SET is_active = (last_nav_date >= %s)
            WHERE last_seen_at >= NOW() - INTERVAL '2 days'
               OR last_nav_date = %s
            """,
            (stale_cutoff, reference_date),
        )
        conn.commit()
    return stats


def refresh_active_flags() -> dict[str, int]:
    """A scheme is active when its last NAV is within N days of the newest NAV anywhere."""
    settings = get_settings()
    with connection() as conn:
        conn.execute(
            """
            UPDATE scheme_master SET is_active = (
                last_nav_date IS NOT NULL
                AND last_nav_date >= (SELECT MAX(last_nav_date) FROM scheme_master) - %s
            )
            """,
            (settings.active_nav_stale_days,),
        )
        row = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE is_active) AS a, COUNT(*) AS t FROM scheme_master"
        ).fetchone()
        conn.commit()
    return {"active": int(row["a"]), "total": int(row["t"])}


def sync_nav_daily() -> dict[str, object]:
    text, archive_key = download_navall()
    rows, latest = parse_navall(text)
    file_date = latest.date() if latest else None
    stats = upsert_navall_rows(rows, file_date)
    stats["archive_key"] = archive_key
    stats["file_as_of"] = file_date.isoformat() if file_date else None
    stats["row_count"] = len(rows)
    return stats
