from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from mfscreener.config import get_settings
from mfscreener.db import connection


def archive_raw(
    *,
    source: str,
    filename: str,
    body: bytes | BinaryIO,
    content_type: str = "application/octet-stream",
) -> str:
    """Store raw AMFI files on disk under ``.data/raw-amfi`` and log in Postgres."""
    settings = get_settings()
    ts = datetime.now(UTC).strftime("%Y/%m/%d")
    key = f"{source}/{ts}/{filename}"
    if isinstance(body, bytes):
        payload = body
    else:
        payload = body.read()
    checksum = hashlib.sha256(payload).hexdigest()
    base = settings.local_archive_dir or ".data/raw-amfi"
    path = Path(base) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO raw_archive (object_key, source, content_type, byte_size, checksum_sha256)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (object_key) DO UPDATE SET
                byte_size = EXCLUDED.byte_size,
                checksum_sha256 = EXCLUDED.checksum_sha256,
                ingested_at = NOW()
            """,
            (key, source, content_type, len(payload), checksum),
        )
        conn.commit()
    return key
