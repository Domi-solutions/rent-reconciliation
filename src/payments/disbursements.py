"""
Disbursement engine: calculate and execute monthly landlord payouts.
Reads payment_transactions for the period; creates disbursements record.
"""

import logging
from datetime import datetime, timezone

from src.database.db import get_connection, generate_id

logger = logging.getLogger(__name__)


def _get_confirmed_payout_owner(conn, property_id):
    """
    Return the first owner for this property whose payout account is confirmed
    and past the 48-hour hold period, or None.

    Only the owner can set payout_mpesa via their portal — admin has no write path.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    return conn.execute(
        """
        SELECT o.id, o.name, o.payout_mpesa
        FROM owners o
        JOIN property_owners po ON po.owner_id = o.id
        WHERE po.property_id = ?
          AND o.payout_confirmed = 1
          AND o.payout_mpesa IS NOT NULL
          AND o.payout_active_at <= ?
        ORDER BY po.created_at
        LIMIT 1
        """,
        (property_id, now),
    ).fetchone()


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
    Raises ValueError if no owner has a confirmed, active payout account.
    """
    existing = conn.execute(
        "SELECT id FROM disbursements WHERE property_id = ? AND period = ?",
        (property_id, period),
    ).fetchone()
    if existing:
        logger.info("Disbursement already exists for %s %s — skipping", property_id, period)
        return existing["id"]

    owner = _get_confirmed_payout_owner(conn, property_id)
    if not owner:
        # Raise a platform alert so the operator can chase the owner to set their account.
        from src.platform.guardian import raise_alert
        prop = conn.execute("SELECT name, organization_id FROM properties WHERE id = ?", (property_id,)).fetchone()
        prop_name = prop['name'] if prop else property_id
        org_id = prop['organization_id'] if prop else None
        raise_alert(
            conn, 'disbursement_blocked',
            f"Disbursement for {prop_name} ({period}) blocked: no owner has a confirmed, "
            f"active payout account. Owner must set their M-Pesa number via the owner portal.",
            org_id=org_id, property_id=property_id, severity='critical',
        )
        raise ValueError(
            f"No confirmed payout account for property {property_id}. "
            f"Owner must set their M-Pesa number via the owner portal before disbursement can proceed."
        )

    data = calculate_disbursement(conn, property_id, period)
    disb_id = generate_id("DISB")
    conn.execute(
        """
        INSERT INTO disbursements
            (id, property_id, period, total_collected, fee_rate, fee_amount, net_amount,
             recipient_account, status, method, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'mpesa_b2c', CURRENT_TIMESTAMP)
        """,
        (
            disb_id,
            property_id,
            period,
            data["total_collected"],
            data["fee_rate"],
            data["fee_amount"],
            data["net_amount"],
            owner["payout_mpesa"],
        ),
    )
    logger.info(
        "Disbursement %s created: %s %s — KES %.2f net → %s",
        disb_id, property_id, period, data["net_amount"], owner["payout_mpesa"],
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
