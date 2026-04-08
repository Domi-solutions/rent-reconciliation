"""Scheduled jobs for the Domi agent layer."""

from datetime import datetime

from src.database.db import generate_id, get_connection
from src.agent.detector import check_pending_tasks, detect_anomalies, detect_followups
from src.agent.briefings import generate_weekly_digest
from src.agent.router import route_message


def _active_property_ids(conn):
    rows = conn.execute(
        "SELECT id FROM properties WHERE status = 'active'"
    ).fetchall()
    return [row["id"] for row in rows]


def daily_snapshot_job():
    """Write one balance snapshot per unit per day."""
    snapshot_date = datetime.utcnow().date().isoformat()
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            rows = conn.execute(
                """
                SELECT unit_id, total_charged, total_paid, balance
                FROM unit_balances
                WHERE property_id = ?
                """,
                (property_id,),
            ).fetchall()

            for row in rows:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO balance_snapshots
                    (id, property_id, unit_id, snapshot_date, balance, total_charged, total_paid)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_id("BSNAP"),
                        property_id,
                        row["unit_id"],
                        snapshot_date,
                        float(row["balance"] or 0),
                        float(row["total_charged"] or 0),
                        float(row["total_paid"] or 0),
                    ),
                )


def anomaly_check_job():
    """Run anomaly and follow-up detection across all active properties."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            detect_anomalies(conn, property_id)
            detect_followups(conn, property_id)


def morning_briefings_job():
    """Run daily task detection for all properties."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            check_pending_tasks(conn, property_id)


def weekly_digest_job():
    """Generate weekly digest text for each active property."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            generate_weekly_digest(conn, property_id)


def send_monthly_checkins_job(property_id):
    """Send one monthly tenant check-in message per active tenant."""
    period = datetime.utcnow().strftime("%Y-%m")
    outbound_messages = []
    with get_connection() as conn:
        tenants = conn.execute(
            """
            SELECT t.id AS tenant_id, t.name, t.phone, t.unit_id, u.unit_number
            FROM tenants t
            JOIN units u ON u.id = t.unit_id
            WHERE t.property_id = ?
              AND t.status = 'active'
              AND t.unit_id IS NOT NULL
              AND t.phone IS NOT NULL
              AND TRIM(t.phone) != ''
            """,
            (property_id,),
        ).fetchall()

        for tenant in tenants:
            already_sent = conn.execute(
                """
                SELECT 1
                FROM messages
                WHERE property_id = ?
                  AND tenant_id = ?
                  AND message_type = 'checkin'
                  AND subject = ?
                LIMIT 1
                """,
                (property_id, tenant["tenant_id"], f"Check-in {period}"),
            ).fetchone()
            if already_sent:
                continue

            body = (
                f"Hello {tenant['name']}, Unit {tenant['unit_number']} check-in for {period}. "
                "How is everything? Reply 1 (good), 2 (small issue), or 3 (urgent), with optional comment."
            )
            outbound_messages.append(
                {
                    "recipient_phone": tenant["phone"],
                    "recipient_role": "tenant",
                    "property_id": property_id,
                    "message_type": "checkin",
                    "subject": f"Check-in {period}",
                    "body": body,
                    "channel": "sms",
                }
            )

    for msg in outbound_messages:
        route_message(msg)


def monthly_checkins_job():
    """Run monthly tenant check-ins across active properties."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            send_monthly_checkins_job(property_id)

