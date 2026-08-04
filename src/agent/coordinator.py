"""Scheduled jobs for the Domi agent layer."""

import json
import logging
from datetime import datetime

from src.database.db import generate_id, get_connection, allocate_payment
from src.agent.detector import check_pending_tasks, detect_anomalies, detect_followups, detect_stale_claims
from src.agent.briefings import generate_weekly_digest
from src.agent.router import route_message
from src.agent.maintainer import send_maintainer_digest

logger = logging.getLogger(__name__)


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
    """Run anomaly, follow-up, and stale claim detection across all active properties."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            detect_anomalies(conn, property_id)
            detect_followups(conn, property_id)
            detect_stale_claims(conn, property_id)


def morning_briefings_job():
    """Run daily task detection for all properties."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            check_pending_tasks(conn, property_id)


def weekly_digest_job():
    """Generate and send weekly digest to property owners via SMS."""
    with get_connection() as conn:
        for property_id in _active_property_ids(conn):
            digest = generate_weekly_digest(conn, property_id)
            owners = conn.execute(
                """
                SELECT o.phone FROM owners o
                JOIN property_owners po ON po.owner_id = o.id
                WHERE po.property_id = ?
                  AND o.phone IS NOT NULL
                  AND TRIM(o.phone) != ''
                """,
                (property_id,),
            ).fetchall()
            for owner in owners:
                route_message({
                    "recipient_phone": owner["phone"],
                    "recipient_role": "owner",
                    "property_id": property_id,
                    "message_type": "weekly_digest",
                    "body": digest,
                })


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


def _format_allocations(allocations: list) -> str:
    """Format FIFO allocation list into human-readable breakdown.
    e.g. 'applied: KES 5,000 to Oct service charge, KES 5,000 to Nov rent'
    """
    month_names = {
        "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
        "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
        "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
    }
    parts = []
    for a in allocations:
        period = a.get("period", "")
        charge_type = a.get("charge_type", "charge")
        amount = a.get("allocated", 0)
        # Convert YYYY-MM to "Mon" label
        if len(period) == 7 and period[4] == "-":
            mon = month_names.get(period[5:], period[5:])
            period_label = mon
        else:
            period_label = period
        parts.append(f"KES {amount:,.0f} to {period_label} {charge_type}")
    if not parts:
        return ""
    return "applied: " + ", ".join(parts)


def maintainer_digest_job():
    """Email the maintainer a weekly system health summary."""
    send_maintainer_digest()


def process_payment_queue():
    """
    Background worker: process pending payment_transactions.
    Runs every 60 seconds. For each pending record:
      - Look up unit and tenant
      - Create payment record in payments table
      - Run FIFO allocator
      - Send SMS confirmation with allocation breakdown
      - Mark processing_status = 'completed'
    On error: mark 'failed' with error_detail.
    """
    with get_connection() as conn:
        pending = conn.execute(
            """
            SELECT id, property_id, unit_id, tenant_id, source,
                   external_reference, phone, amount, raw_callback
            FROM payment_transactions
            WHERE processing_status = 'pending'
              AND raw_callback IS NOT NULL
            ORDER BY received_at ASC
            LIMIT 50
            """,
        ).fetchall()

    for txn in pending:
        try:
            with get_connection() as conn:
                # Re-check status inside its own connection to avoid race
                current = conn.execute(
                    "SELECT processing_status FROM payment_transactions WHERE id = ?",
                    (txn["id"],),
                ).fetchone()
                if not current or current["processing_status"] != "pending":
                    continue

                unit_id = txn["unit_id"]
                tenant_id = txn["tenant_id"]
                property_id = txn["property_id"]

                # If unit not stored at initiation, attempt lookup from callback
                if not unit_id and txn["raw_callback"]:
                    try:
                        cb = json.loads(txn["raw_callback"])
                        # Daraja C2B: AccountReference = unit_number
                        items = (cb.get("Body", {})
                                 .get("stkCallback", {})
                                 .get("CallbackMetadata", {})
                                 .get("Item", []))
                        item_map = {i["Name"]: i.get("Value") for i in items}
                        account_ref = item_map.get("AccountReference") or item_map.get("BillRefNumber")
                        if account_ref and property_id:
                            row = conn.execute(
                                "SELECT id FROM units WHERE property_id = ? AND unit_number = ?",
                                (property_id, str(account_ref).strip()),
                            ).fetchone()
                            if row:
                                unit_id = row["id"]
                    except Exception:
                        pass

                if not unit_id:
                    conn.execute(
                        """
                        UPDATE payment_transactions
                        SET processing_status = 'failed',
                            error_detail = 'unit not found',
                            processed_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (txn["id"],),
                    )
                    logger.warning("payment_transactions %s: unit not found", txn["id"])
                    continue

                # Look up tenant for unit if not stored
                if not tenant_id:
                    t = conn.execute(
                        "SELECT id, phone FROM tenants WHERE unit_id = ? AND status = 'active' LIMIT 1",
                        (unit_id,),
                    ).fetchone()
                    if t:
                        tenant_id = t["id"]

                # Determine payment_date from callback or now
                payment_date = datetime.utcnow().date().isoformat()
                if txn["raw_callback"]:
                    try:
                        cb = json.loads(txn["raw_callback"])
                        items = (cb.get("Body", {})
                                 .get("stkCallback", {})
                                 .get("CallbackMetadata", {})
                                 .get("Item", []))
                        item_map = {i["Name"]: i.get("Value") for i in items}
                        txn_date_raw = str(item_map.get("TransactionDate", ""))
                        # Format: 20231219102115 → 2023-12-19
                        if len(txn_date_raw) >= 8:
                            payment_date = f"{txn_date_raw[:4]}-{txn_date_raw[4:6]}-{txn_date_raw[6:8]}"
                    except Exception:
                        pass

                # Get mpesa_ref from callback for display
                mpesa_ref = txn["external_reference"] or ""
                if txn["raw_callback"]:
                    try:
                        cb = json.loads(txn["raw_callback"])
                        items = (cb.get("Body", {})
                                 .get("stkCallback", {})
                                 .get("CallbackMetadata", {})
                                 .get("Item", []))
                        item_map = {i["Name"]: i.get("Value") for i in items}
                        mpesa_ref = item_map.get("MpesaReceiptNumber") or mpesa_ref
                    except Exception:
                        pass

                # Create payment record (bank_txn_id NULL — Daraja/Pesapal payments)
                payment_id = generate_id("PAY")
                conn.execute(
                    """
                    INSERT INTO payments
                        (id, property_id, unit_id, claim_id, bank_txn_id, statement_id,
                         amount, payment_date, assignment_type, assignment_reason,
                         assigned_by, source, created_at)
                    VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        payment_id,
                        property_id,
                        unit_id,
                        float(txn["amount"]),
                        payment_date,
                        txn["source"],          # assignment_type = 'daraja' | 'pesapal'
                        mpesa_ref,              # assignment_reason stores M-Pesa ref
                        "daraja_callback",      # assigned_by
                        txn["source"],          # source column
                    ),
                )

                # FIFO allocation
                allocations = allocate_payment(conn, payment_id, unit_id, float(txn["amount"]))

                # Link payment_transaction → payment
                conn.execute(
                    """
                    UPDATE payment_transactions
                    SET processing_status = 'completed',
                        processed_at = CURRENT_TIMESTAMP,
                        payment_id = ?,
                        unit_id = ?,
                        tenant_id = ?,
                        property_id = ?
                    WHERE id = ?
                    """,
                    (payment_id, unit_id, tenant_id, property_id, txn["id"]),
                )

                # Build confirmation SMS with FIFO transparency
                amount_fmt = f"{float(txn['amount']):,.0f}"
                breakdown = _format_allocations(allocations)
                if breakdown:
                    sms_body = f"KES {amount_fmt} confirmed. {breakdown}."
                else:
                    sms_body = f"KES {amount_fmt} confirmed."

                # Send to tenant phone
                phone = txn["phone"]
                if not phone and tenant_id:
                    t = conn.execute("SELECT phone FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
                    if t:
                        phone = t["phone"]

                if phone:
                    route_message({
                        "recipient_phone": phone,
                        "recipient_role": "tenant",
                        "property_id": property_id,
                        "message_type": "payment_confirmation",
                        "body": sms_body,
                    })

                logger.info(
                    "Processed payment %s for unit %s — KES %.2f",
                    payment_id, unit_id, float(txn["amount"]),
                )

        except Exception as e:
            logger.exception("Failed to process payment_transaction %s", txn["id"])
            try:
                with get_connection() as conn:
                    conn.execute(
                        """
                        UPDATE payment_transactions
                        SET processing_status = 'failed',
                            error_detail = ?,
                            processed_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (str(e)[:500], txn["id"]),
                    )
            except Exception:
                pass

