"""Dead-man's-switch heartbeats for scheduled jobs, via healthchecks.io.

Wired into the APScheduler listener in app.py — every job ping()s on
success or failure. If a job stops running entirely (process crash,
scheduler dies, restart drops it from the in-memory job store), no ping
arrives and healthchecks.io alerts MAINTAINER_EMAIL on its own, without
needing this app to be alive to raise the alarm.

Env vars:
  HEALTHCHECKS_PING_KEY — ping key from https://healthchecks.io account
                          Settings. If unset, ping() is a no-op (mirrors
                          the SMTP-unconfigured pattern in email.py).
"""

import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_PING_BASE = "https://hc-ping.com"


def ping(job_id, success=True):
    """Ping healthchecks.io for job_id. First ping for a given job_id
    auto-creates the check in the dashboard. success=False hits the
    /fail suffix, which healthchecks.io treats as an immediate failure
    regardless of schedule."""
    key = os.environ.get('HEALTHCHECKS_PING_KEY', '').strip()
    if not key:
        return

    url = f"{_PING_BASE}/{key}/{job_id}"
    if not success:
        url += "/fail"

    try:
        urllib.request.urlopen(url, timeout=5)
    except Exception as exc:
        logger.warning("Heartbeat ping failed for %s: %s", job_id, exc)
