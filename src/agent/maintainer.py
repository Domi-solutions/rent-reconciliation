"""Weekly system health digest emailed to the platform maintainer.

Triggered by maintainer_digest_job in coordinator.py (Mondays 08:00 EAT).
Also used by the Flask 500 error handler for critical alerts.

Env vars required:
  MAINTAINER_EMAIL — recipient address (e.g. lincksmorara@gmail.com)
  SMTP_*           — standard email config (see src/messaging/email.py)
"""

import logging
import os
import traceback
from datetime import datetime, timedelta

from src.database.db import get_connection
from src.messaging.email import send_email
from src.messaging.outbox import log_outbox

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get('APP_BASE_URL', 'https://rent-reconciliation.fly.dev')


def _maintainer_email():
    return os.environ.get('MAINTAINER_EMAIL', '').strip()


def build_maintainer_digest(conn):
    """Return a plain-text weekly health summary."""
    now = datetime.utcnow()
    week_ago = (now - timedelta(days=7)).isoformat()
    month_start = now.strftime('%Y-%m-01')

    lines = [
        f"Domi System Health — {now.strftime('%A %d %B %Y')} (UTC)",
        "=" * 52,
        "",
    ]

    # --- Properties + tenants ---
    props = conn.execute(
        "SELECT COUNT(*) FROM properties WHERE status = 'active'"
    ).fetchone()[0]
    tenants = conn.execute(
        "SELECT COUNT(*) FROM tenants WHERE status = 'active'"
    ).fetchone()[0]
    lines += [
        f"Properties: {props} active",
        f"Tenants:    {tenants} active",
        "",
    ]

    # --- Payments this month ---
    pay_count = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE payment_date >= ?", (month_start,)
    ).fetchone()[0]
    pay_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE payment_date >= ?", (month_start,)
    ).fetchone()[0]
    lines += [
        f"Payments verified this month: {pay_count}  (KES {float(pay_total):,.0f})",
        "",
    ]

    # --- Pending / flagged claims ---
    pending_claims = conn.execute(
        "SELECT COUNT(*) FROM payment_claims WHERE status = 'pending'"
    ).fetchone()[0]
    flagged_claims = conn.execute(
        "SELECT COUNT(*) FROM payment_claims WHERE status = 'flagged' AND admin_cleared = 0"
    ).fetchone()[0]
    if pending_claims or flagged_claims:
        lines += [
            f"Payment claims — pending: {pending_claims}  flagged (uncleared): {flagged_claims}",
            "",
        ]

    # --- Unassigned bank transactions ---
    unassigned = conn.execute(
        """
        SELECT COUNT(*) FROM bank_transactions bt
        WHERE NOT EXISTS (SELECT 1 FROM payments p WHERE p.bank_txn_id = bt.id)
          AND (bt.ignored IS NULL OR bt.ignored = 0)
        """
    ).fetchone()[0]
    if unassigned:
        lines += [
            f"Unassigned bank transactions: {unassigned}  — someone needs to action these",
            "",
        ]

    # --- Parse errors (7 days) ---
    try:
        parse_errors = conn.execute(
            "SELECT COUNT(*) FROM statement_parse_errors WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]
        if parse_errors:
            lines += [f"Bank statement parse errors (7 days): {parse_errors}", ""]
    except Exception:
        pass

    # --- Open platform alerts ---
    try:
        open_alerts = conn.execute(
            "SELECT COUNT(*) FROM platform_alerts WHERE dismissed_at IS NULL"
        ).fetchone()[0]
        if open_alerts:
            lines += [
                f"Open platform alerts: {open_alerts}  — check {_BASE_URL}/platform/alerts",
                "",
            ]
    except Exception:
        pass

    # --- Last admin activity ---
    last_admin = conn.execute("SELECT MAX(timestamp) FROM audit_log").fetchone()[0]
    if last_admin:
        lines += [f"Last admin activity: {last_admin[:16]} UTC", ""]
        last_dt = datetime.fromisoformat(last_admin)
        days_idle = (now - last_dt).days
        if days_idle >= 14:
            lines += [
                f"WARNING: No admin activity for {days_idle} days.",
                "Is anyone actively using the system?",
                "",
            ]
    else:
        lines += ["WARNING: No entries in audit log — system may be unused.", ""]

    # --- Services configured ---
    svc = {
        'SMTP email':   bool(os.environ.get('SMTP_HOST')),
        'SMS (AT)':     bool(os.environ.get('AT_API_KEY')),
        'Anthropic AI': bool(os.environ.get('ANTHROPIC_API_KEY')),
        'M-Pesa Daraja': bool(os.environ.get('DARAJA_CONSUMER_KEY')),
    }
    missing = [k for k, v in svc.items() if not v]
    if missing:
        lines += [
            f"Services NOT configured: {', '.join(missing)}",
            "",
        ]

    lines += [
        "---",
        f"Admin: {_BASE_URL}",
        f"Platform: {_BASE_URL}/platform",
        "This is an automated message. Reply is not monitored.",
    ]
    return "\n".join(lines)


def send_maintainer_digest():
    """Build and send the weekly health digest email."""
    to_email = _maintainer_email()
    if not to_email:
        logger.info("MAINTAINER_EMAIL not set — skipping maintainer digest")
        return

    with get_connection() as conn:
        body = build_maintainer_digest(conn)

    subject = f"[Domi] System Health — {datetime.utcnow().strftime('%d %b %Y')}"
    ok, err = send_email(to_email, subject, body, to_name='Domi Maintainer')
    status = 'sent' if ok else 'simulated'
    log_outbox(
        channel='email',
        body=body,
        to_name='Domi Maintainer',
        to_email=to_email,
        subject=subject,
        status=status,
        error=err,
        message_type='maintainer_digest',
    )
    if ok:
        logger.info("Maintainer digest sent to %s", to_email)
    else:
        logger.warning("Maintainer digest not delivered (%s) — logged to platform_outbox", err)


def send_error_alert(error_summary, path=None, method=None, tb=None):
    """Email the maintainer immediately on an unhandled 500 error."""
    to_email = _maintainer_email()
    if not to_email:
        return

    now = datetime.utcnow()
    lines = [
        f"[Domi] Unhandled error at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "=" * 52,
        "",
        f"Error:   {error_summary}",
    ]
    if method and path:
        lines += [f"Request: {method} {path}", ""]
    if tb:
        lines += ["Traceback:", "----------", tb, ""]
    lines += [f"Admin: {_BASE_URL}"]

    body = "\n".join(lines)
    subject = f"[Domi] ERROR — {error_summary[:60]}"
    ok, err = send_email(to_email, subject, body, to_name='Domi Maintainer')
    status = 'sent' if ok else 'simulated'
    log_outbox(
        channel='email',
        body=body,
        to_name='Domi Maintainer',
        to_email=to_email,
        subject=subject,
        status=status,
        error=err,
        message_type='error_alert',
    )
