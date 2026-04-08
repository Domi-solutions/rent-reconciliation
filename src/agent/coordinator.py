"""Scheduled jobs for the Domi agent layer."""

from datetime import datetime

from src.database.db import generate_id, get_connection
from src.agent.detector import check_pending_tasks, detect_anomalies, detect_followups


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
    """Reserved hook for weekly digest generation/delivery."""
    with get_connection() as conn:
        for _property_id in _active_property_ids(conn):
            # Phase C wires digest generation and delivery.
            pass

