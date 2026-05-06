"""
Pesapal card checkout integration — stub.
Wired up once PESAPAL_CONSUMER_KEY and PESAPAL_CONSUMER_SECRET are set.
Callback IPN: POST /inbound/payment/pesapal
"""

import os
import logging

logger = logging.getLogger(__name__)


def create_checkout(amount: float, account_ref: str, description: str,
                    redirect_url: str, phone: str = None) -> dict:
    """
    Initiate Pesapal hosted checkout.
    Returns {'success': bool, 'redirect_url': str|None, 'order_tracking_id': str|None, 'error': str|None}
    """
    key = os.environ.get("PESAPAL_CONSUMER_KEY", "")
    secret = os.environ.get("PESAPAL_CONSUMER_SECRET", "")
    if not key or not secret:
        logger.warning("Pesapal credentials not set — card checkout unavailable")
        return {"success": False, "redirect_url": None, "order_tracking_id": None,
                "error": "Card payment not yet configured"}

    # Full implementation pending Pesapal merchant account approval
    return {"success": False, "redirect_url": None, "order_tracking_id": None,
            "error": "Pesapal integration pending"}


def parse_ipn_callback(body: dict) -> dict:
    """
    Parse Pesapal IPN callback.
    Returns normalised dict: order_tracking_id, status, amount, mpesa_ref
    """
    return {
        "order_tracking_id": body.get("OrderTrackingId"),
        "status": body.get("OrderPaymentStatus"),
        "amount": body.get("Amount"),
        "mpesa_ref": body.get("TransactionCode"),
    }
