"""Dead-man's-switch heartbeats for scheduled jobs, via healthchecks.io.

Wired into the APScheduler listener in app.py — every job ping()s on
success or failure. If a job stops running entirely (process crash,
scheduler dies, restart drops it from the in-memory job store), no ping
arrives and healthchecks.io alerts MAINTAINER_EMAIL on its own, without
needing this app to be alive to raise the alarm.

Checks were provisioned once via the Healthchecks API (project:
lincksmorara@gmail.com on healthchecks.io) with schedules matching the
actual UTC cron triggers in app.py — note the "EAT" hour labels in
app.py's comments and .agent/jobs.yaml don't reflect real EAT conversion
(APScheduler runs these hour= values as UTC, not EAT as the comments
claim); the checks below use the real UTC times so alerts fire at the
right offset. Each PING_URLS entry is a per-check URL, not an account
secret — exposing one only lets someone spoof a "success" signal for
that specific check, per healthchecks.io's own threat model.

To add a job: create its check at healthchecks.io (or via the API) and
add its ping_url here — no other wiring needed.
"""

import logging
import urllib.request

logger = logging.getLogger(__name__)

PING_URLS = {
    "daily_snapshot_job": "https://hc-ping.com/d54c500a-8fa9-4ceb-8ef3-c520a77671b6",
    "anomaly_check_job": "https://hc-ping.com/adbdff0a-b6e7-4757-9c7c-46a60996e3c0",
    "morning_briefings_job": "https://hc-ping.com/37d9440c-792a-4a27-bd7f-ff2780b2999b",
    "maintainer_digest_job": "https://hc-ping.com/2b77bb83-085e-482c-975d-f207a1eb1ea5",
    "weekly_digest_job": "https://hc-ping.com/862a92b3-cc7a-433d-8494-d7ecb52947dc",
    "disbursement_job": "https://hc-ping.com/a3f3c16e-8af7-4a6f-b958-7c5b46e23b82",
    "monthly_checkins_job": "https://hc-ping.com/375d73e2-cf6d-4ba4-afb0-b2d369d3bf3b",
    "process_payment_queue": "https://hc-ping.com/0444dd0b-d456-40cf-aba9-5dcc78e27979",
}


def ping(job_id, success=True):
    """Ping healthchecks.io for job_id. No-ops if job_id has no registered
    check (e.g. a new job added without updating PING_URLS yet).
    success=False hits the /fail suffix — treated as an immediate failure
    regardless of schedule."""
    url = PING_URLS.get(job_id)
    if not url:
        return

    if not success:
        url += "/fail"

    try:
        urllib.request.urlopen(url, timeout=5)
    except Exception as exc:
        logger.warning("Heartbeat ping failed for %s: %s", job_id, exc)
