from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config

from mfscreener.config import get_settings
from mfscreener.db import connection


def _local_archive(key: str, payload: bytes) -> None:
    settings = get_settings()
    base = settings.local_archive_dir or ".data/raw-amfi"
    path = Path(base) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def archive_raw(
    *,
    source: str,
    filename: str,
    body: bytes | BinaryIO,
    content_type: str = "application/octet-stream",
) -> str:
    settings = get_settings()
    client = _s3_client()
    ts = datetime.now(UTC).strftime("%Y/%m/%d")
    key = f"{source}/{ts}/{filename}"
    if isinstance(body, bytes):
        payload = body
    else:
        payload = body.read()
    checksum = hashlib.sha256(payload).hexdigest()
    try:
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=payload,
            ContentType=content_type,
        )
    except Exception:
        _local_archive(key, payload)
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
