"""
Disbursement engine: calculate and execute monthly landlord payouts.
Reads payment_transactions for the period; creates disbursements record.
"""

import logging
from datetime import datetime

from src.database.db import get_connection, generate_id

logger = logging.getLogger(__name__)


def calculate_disbursement(conn, property_id: str, period: str) -> dict:
    """
    Calculate disbursement for a property and period (YYYY-MM).
    Returns:
      {total_collected, fee_rate, fee_amount, net_amount, unit_breakdown, period}
    Does NOT write anything to the DB.
    """
    prop = conn.execute(
        "SELECT name, management_fee_rate FROM properties WHERE id = ?",
        (property_id,),
    ).fetchone()
    if not prop:
        raise ValueError(f"Property {property_id} not found")

    fee_rate = float(prop["management_fee_rate"] or 0.08)

    # Sum all completed Daraja/Pesapal payments for this period
    # Period filter: payment_date starts with YYYY-MM
    rows = conn.execute(
        """
        SELECT pt.unit_id, pt.amount
        FROM payment_transactions pt
        WHERE pt.property_id = ?
          AND pt.processing_status = 'completed'
          AND pt.source IN ('daraja', 'pesapal')
          AND strftime('%Y-%m', pt.processed_at) = ?
        """,
        (property_id, period),
    ).fetchall()

    total_collected = sum(float(r["amount"]) for r in rows)
    fee_amount = round(total_collected * fee_rate, 2)
    net_amount = round(total_collected - fee_amount, 2)

    # Unit breakdown
    unit_totals = {}
    for r in rows:
        uid = r["unit_id"] or "unknown"
        unit_totals[uid] = unit_totals.get(uid, 0) + float(r["amount"])

    return {
        "period": period,
        "total_collected": total_collected,
        "fee_rate": fee_rate,
        "fee_amount": fee_amount,
        "net_amount": net_amount,
        "unit_breakdown": unit_totals,
    }


def execute_disbursement(conn, property_id: str, period: str) -> str:
    """
    Create a disbursements record for a period.
    Returns the disbursement id.
    Skips if a disbursement record already exists for this property+period.
    """
    existing = conn.execute(
        "SELECT id FROM disbursements WHERE property_id = ? AND period = ?",
        (property_id, period),
    ).fetchone()
    if existing:
        logger.info("Disbursement already exists for %s %s — skipping", property_id, period)
        return existing["id"]

    data = calculate_disbursement(conn, property_id, period)
    disb_id = generate_id("DISB")
    conn.execute(
        """
        INSERT INTO disbursements
            (id, property_id, period, total_collected, fee_rate, fee_amount, net_amount,
             status, method, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'mpesa_b2c', CURRENT_TIMESTAMP)
        """,
        (
            disb_id,
            property_id,
            period,
            data["total_collected"],
            data["fee_rate"],
            data["fee_amount"],
            data["net_amount"],
        ),
    )
    logger.info(
        "Disbursement %s created: %s %s — KES %.2f net",
        disb_id, property_id, period, data["net_amount"],
    )
    return disb_id


def scheduled_disbursement_job():
    """APScheduler entry point: run disbursements for all active properties on the 10th."""
    period = datetime.utcnow().strftime("%Y-%m")
    with get_connection() as conn:
        properties = conn.execute(
            "SELECT id FROM properties WHERE status = 'active'"
        ).fetchall()
        for prop in properties:
            try:
                execute_disbursement(conn, prop["id"], period)
            except Exception as e:
                logger.error("Disbursement failed for %s: %s", prop["id"], e)
