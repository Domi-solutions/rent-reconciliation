"""Automated off-server database backups, uploaded to Cloudflare R2.

Triggered by backup_job in coordinator.py (daily). Uses sqlite3's own
backup API rather than a raw file copy — the live DB is in WAL mode and
under active write traffic, so a raw copy risks capturing a torn,
inconsistent snapshot mid-write. The backup API produces a
transactionally-consistent copy regardless of concurrent writers.

Env vars required (see .agent/env.yaml):
  R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME

Retention: 30 days, enforced by deleting older objects after each
successful upload — bounds storage growth without a separate cleanup job.
"""

import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

import boto3

from src.database.db import get_db_path

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 30
_PREFIX = "backups/"


def _r2_client():
    endpoint = os.environ.get('R2_ENDPOINT_URL', '').strip()
    key = os.environ.get('R2_ACCESS_KEY_ID', '').strip()
    secret = os.environ.get('R2_SECRET_ACCESS_KEY', '').strip()
    if not (endpoint and key and secret):
        return None
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name='auto',
    )


def _make_consistent_snapshot():
    """Return path to a temp file containing a transactionally-consistent
    copy of the live DB, made via sqlite3's backup API."""
    src = sqlite3.connect(get_db_path())
    fd, tmp_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    dst = sqlite3.connect(tmp_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return tmp_path


def _prune_old_backups(client, bucket):
    cutoff = datetime.utcnow() - timedelta(days=_RETENTION_DAYS)
    resp = client.list_objects_v2(Bucket=bucket, Prefix=_PREFIX)
    for obj in resp.get('Contents', []):
        if obj['LastModified'].replace(tzinfo=None) < cutoff:
            client.delete_object(Bucket=bucket, Key=obj['Key'])
            logger.info("Pruned old backup: %s", obj['Key'])


def run_backup():
    """Snapshot the live DB and upload it to R2. Raises on failure so the
    scheduler's EVENT_JOB_ERROR listener picks it up (heartbeat fail ping
    + immediate email) — this function must not swallow errors."""
    client = _r2_client()
    if client is None:
        logger.warning("R2 credentials not configured — skipping backup")
        return

    bucket = os.environ['R2_BUCKET_NAME']
    tmp_path = _make_consistent_snapshot()
    try:
        timestamp = datetime.utcnow().strftime('%Y-%m-%d_%H%M%S')
        key = f"{_PREFIX}rent_{timestamp}.db"
        client.upload_file(tmp_path, bucket, key)
        logger.info("Backup uploaded: %s", key)
        _prune_old_backups(client, bucket)
    finally:
        os.remove(tmp_path)
