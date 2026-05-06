"""
Payment webhook handlers — write-and-return-200 only.
Never process inline; background worker (process_payment_queue) handles everything.
Exempt from admin auth (webhook endpoints).
"""

import json
import logging

from flask import Blueprint, request, jsonify
from src.database.db import get_connection, generate_id
from src.payments.daraja import parse_stk_callback

logger = logging.getLogger(__name__)

payment_bp = Blueprint("payment", __name__, url_prefix="/inbound/payment")


@payment_bp.route("/mpesa", methods=["POST"])
def mpesa_callback():
    """Daraja STK Push callback. Write to payment_transactions, return 200 immediately."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        parsed = parse_stk_callback(body)
        checkout_request_id = parsed.get("checkout_request_id")

        if not checkout_request_id:
            logger.warning("Daraja callback missing CheckoutRequestID: %s", body)
            return "", 200  # Always 200 to prevent Daraja retries on bad payload

        raw = json.dumps(body)

        with get_connection() as conn:
            # Check if we already have a record for this checkout_request_id
            existing = conn.execute(
                "SELECT id, processing_status FROM payment_transactions WHERE external_reference = ?",
                (checkout_request_id,),
            ).fetchone()

            if existing:
                # Callback arrived — update the record with actual result
                if parsed["result_code"] == 0:
                    conn.execute(
                        """
                        UPDATE payment_transactions
                        SET raw_callback = ?, amount = ?, processing_status = 'pending'
                        WHERE external_reference = ?
                        """,
                        (raw, parsed["amount"], checkout_request_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE payment_transactions
                        SET raw_callback = ?, processing_status = 'failed',
                            error_detail = ?, processed_at = CURRENT_TIMESTAMP
                        WHERE external_reference = ?
                        """,
                        (raw, parsed["result_desc"], checkout_request_id),
                    )
            else:
                # Unexpected direct Paybill payment (no STK Push pre-record)
                # Write with amount=0 placeholder; worker will fill from callback
                amount = parsed.get("amount") or 0
                status = "pending" if parsed["result_code"] == 0 else "failed"
                error = parsed["result_desc"] if parsed["result_code"] != 0 else None
                txn_id = generate_id("PTXN")
                conn.execute(
                    """
                    INSERT INTO payment_transactions
                        (id, source, external_reference, phone, amount, raw_callback,
                         processing_status, error_detail)
                    VALUES (?, 'daraja', ?, ?, ?, ?, ?, ?)
                    """,
                    (txn_id, checkout_request_id, parsed.get("phone"), amount,
                     raw, status, error),
                )

    except Exception:
        logger.exception("Error processing Daraja callback")

    return "", 200


@payment_bp.route("/pesapal", methods=["POST"])
def pesapal_callback():
    """Pesapal IPN handler. Write to payment_transactions, return 200 immediately."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        order_tracking_id = body.get("OrderTrackingId")
        amount = body.get("Amount") or 0
        raw = json.dumps(body)

        if not order_tracking_id:
            return "", 200

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM payment_transactions WHERE external_reference = ?",
                (order_tracking_id,),
            ).fetchone()
            if not existing:
                txn_id = generate_id("PTXN")
                conn.execute(
                    """
                    INSERT INTO payment_transactions
                        (id, source, external_reference, amount, raw_callback, processing_status)
                    VALUES (?, 'pesapal', ?, ?, ?, 'pending')
                    """,
                    (txn_id, order_tracking_id, float(amount), raw),
                )
    except Exception:
        logger.exception("Error processing Pesapal callback")

    return "", 200
