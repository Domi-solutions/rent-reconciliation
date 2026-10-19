"""
Payment webhook handlers — write-and-return-200 only.
Never process inline; background worker (process_payment_queue) handles everything.
Exempt from admin auth (webhook endpoints).
"""

import json
import logging
import os
import threading

from flask import Blueprint, request, jsonify
from src.database.db import get_connection, generate_id
from src.messaging.delivery import send_sms
from src.payments import mpesa

logger = logging.getLogger(__name__)

payment_bp = Blueprint("payment", __name__, url_prefix="/inbound/payment")


def _book_now(txn_id, base_url):
    """Book in the background right away so the tenant's screen and SMS update
    within seconds; the 60-second worker is only the safety net."""
    def run():
        try:
            sms = mpesa.book(txn_id, base_url)
            if sms:
                send_sms([sms["phone"]], sms["body"])
        except Exception:
            logger.exception("Instant booking of %s failed; the worker will retry", txn_id)
    threading.Thread(target=run, daemon=True).start()


@payment_bp.route("/mpesa", methods=["POST"])
def mpesa_callback():
    """Daraja STK Push callback. Record, return 200 at once; booking happens off-thread."""
    if not mpesa.callback_allowed(request):
        logger.warning("Rejected M-Pesa STK callback: bad token or source")
        return "", 200          # 200 so nothing retries; nothing was recorded
    try:
        body = request.get_json(force=True, silent=True) or {}
        txn_id = mpesa.record_stk_callback(body)
        if txn_id:
            _book_now(txn_id, os.environ.get("APP_BASE_URL") or request.host_url)
    except Exception:
        logger.exception("Error processing Daraja callback")
    return "", 200


@payment_bp.route("/c2b/validation", methods=["POST"])
def c2b_validation():
    """Safaricom asks whether to accept a paybill payment: accept known account numbers only."""
    if not mpesa.callback_allowed(request):
        return jsonify({"ResultCode": "C2B00016", "ResultDesc": "Rejected"}), 200
    try:
        return jsonify(mpesa.validate_c2b(request.get_json(force=True, silent=True) or {})), 200
    except Exception:
        logger.exception("C2B validation error")
        return jsonify({"ResultCode": "0", "ResultDesc": "Accepted"}), 200   # never lose a payment to our bug


@payment_bp.route("/c2b/confirmation", methods=["POST"])
def c2b_confirmation():
    """A paybill payment has been made. Record, return at once; booking happens off-thread."""
    if not mpesa.callback_allowed(request):
        logger.warning("Rejected M-Pesa C2B confirmation: bad token or source")
        return jsonify({"ResultCode": 0, "ResultDesc": "Success"}), 200
    try:
        txn_id = mpesa.record_c2b_confirmation(request.get_json(force=True, silent=True) or {})
        if txn_id:
            _book_now(txn_id, os.environ.get("APP_BASE_URL") or request.host_url)
    except Exception:
        logger.exception("Error recording C2B confirmation")
    return jsonify({"ResultCode": 0, "ResultDesc": "Success"}), 200


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
