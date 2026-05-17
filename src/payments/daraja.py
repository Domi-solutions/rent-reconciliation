"""
Daraja (Safaricom M-Pesa API) integration.
Handles STK Push initiation and OAuth token management.
Set DARAJA_ENV=sandbox for sandbox mode (default); =production for live.
"""

import os
import base64
import json
import logging
from datetime import datetime

import urllib.request
import urllib.error

from src.utils.phone import normalize_to_daraja as normalize_phone

logger = logging.getLogger(__name__)

SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE = "https://api.safaricom.co.ke"


def _base_url():
    return PRODUCTION_BASE if os.environ.get("DARAJA_ENV") == "production" else SANDBOX_BASE


def _get_access_token():
    """Fetch short-lived OAuth bearer token from Daraja.
    Returns token string or raises RuntimeError on failure."""
    key = os.environ.get("DARAJA_CONSUMER_KEY", "")
    secret = os.environ.get("DARAJA_CONSUMER_SECRET", "")
    if not key or not secret:
        raise RuntimeError("DARAJA_CONSUMER_KEY and DARAJA_CONSUMER_SECRET must be set")

    credentials = base64.b64encode(f"{key}:{secret}".encode()).decode()
    url = f"{_base_url()}/oauth/v1/generate?grant_type=client_credentials"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {credentials}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Daraja OAuth failed ({e.code}): {body}") from e


def stk_push(phone: str, amount: int, account_ref: str, description: str) -> dict:
    """
    Initiate M-Pesa STK Push to tenant phone.
    account_ref = unit number (e.g. 'A7') — used to map payment to unit on callback.
    amount must be a whole number (KES, no decimals).
    Returns {'success': bool, 'checkout_request_id': str|None, 'error': str|None}
    """
    shortcode = os.environ.get("DARAJA_SHORTCODE", "")
    passkey = os.environ.get("DARAJA_PASSKEY", "")
    callback_url = os.environ.get("DARAJA_CALLBACK_URL", "")

    if not all([shortcode, passkey, callback_url]):
        # Dev/sandbox mode: return a fake checkout_request_id for UI testing
        if os.environ.get("DARAJA_ENV") != "production":
            fake_id = f"sandbox_ws_CO_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{phone[-4:]}"
            logger.warning("Daraja credentials not set — returning sandbox fake checkout_request_id")
            return {"success": True, "checkout_request_id": fake_id, "error": None}
        return {"success": False, "checkout_request_id": None, "error": "Daraja not configured"}

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
    phone_normalized = normalize_phone(phone)

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone_normalized,
        "PartyB": shortcode,
        "PhoneNumber": phone_normalized,
        "CallBackURL": callback_url,
        "AccountReference": account_ref,
        "TransactionDesc": description,
    }

    try:
        token = _get_access_token()
        url = f"{_base_url()}/mpesa/stkpush/v1/processrequest"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            if data.get("ResponseCode") == "0":
                return {
                    "success": True,
                    "checkout_request_id": data.get("CheckoutRequestID"),
                    "error": None,
                }
            return {
                "success": False,
                "checkout_request_id": None,
                "error": data.get("ResponseDescription", "STK Push failed"),
            }
    except RuntimeError as e:
        logger.error("Daraja STK Push error: %s", e)
        return {"success": False, "checkout_request_id": None, "error": str(e)}
    except Exception as e:
        logger.error("Daraja STK Push unexpected error: %s", e)
        return {"success": False, "checkout_request_id": None, "error": "Payment initiation failed"}


def parse_stk_callback(body: dict) -> dict:
    """
    Parse the Daraja STK Push callback body.
    Returns a normalised dict with keys:
      checkout_request_id, result_code, result_desc,
      amount, mpesa_ref, phone, txn_date (all may be None on failure)
    """
    callback = body.get("Body", {}).get("stkCallback", {})
    result_code = callback.get("ResultCode")
    result_desc = callback.get("ResultDesc", "")
    checkout_request_id = callback.get("CheckoutRequestID")

    amount = mpesa_ref = phone = txn_date = None
    if result_code == 0:
        items = callback.get("CallbackMetadata", {}).get("Item", [])
        item_map = {i["Name"]: i.get("Value") for i in items}
        amount = item_map.get("Amount")
        mpesa_ref = item_map.get("MpesaReceiptNumber")
        phone = str(item_map.get("PhoneNumber", ""))
        txn_date = str(item_map.get("TransactionDate", ""))

    return {
        "checkout_request_id": checkout_request_id,
        "result_code": result_code,
        "result_desc": result_desc,
        "amount": amount,
        "mpesa_ref": mpesa_ref,
        "phone": phone,
        "txn_date": txn_date,
    }
