"""
Inbound webhook endpoints for SMS (Africa's Talking) and WhatsApp.
Write-and-return-200 pattern. Processing dispatched in background thread.
Exempt from admin auth via /inbound/* path prefix.
"""
import logging
import threading

from flask import Blueprint, request

from src.agent.inbound import process_inbound_message
from src.database.db import get_connection, generate_id
from src.messaging.delivery import _normalize_phone

logger = logging.getLogger(__name__)

inbound_bp = Blueprint("inbound", __name__, url_prefix="/inbound")


def _resolve_sender(conn, raw_phone):
    """Look up sender by phone. Returns (role, entity_id, property_id)."""
    if not raw_phone:
        return "unknown", None, None

    phone = _normalize_phone(raw_phone)

    # Caretakers first — most likely inbound source
    row = conn.execute(
        "SELECT id, property_id FROM caretakers WHERE phone = ? OR phone = ?",
        (phone, raw_phone),
    ).fetchone()
    if row:
        return "caretaker", row["id"], row["property_id"]

    # Owners
    owner = conn.execute(
        "SELECT id FROM owners WHERE phone = ? OR phone = ?", (phone, raw_phone)
    ).fetchone()
    if owner:
        po = conn.execute(
            "SELECT property_id FROM property_owners WHERE owner_id = ? LIMIT 1",
            (owner["id"],),
        ).fetchone()
        return "owner", owner["id"], po["property_id"] if po else None

    # Tenants
    tenant = conn.execute(
        """
        SELECT id, property_id FROM tenants
        WHERE (phone = ? OR phone = ?) AND status = 'active'
        LIMIT 1
        """,
        (phone, raw_phone),
    ).fetchone()
    if tenant:
        return "tenant", tenant["id"], tenant["property_id"]

    return "unknown", None, None


def _dispatch(message_id, raw_text, context):
    """Fire-and-forget: process inbound message in a background thread."""
    def _run():
        try:
            with get_connection() as conn:
                process_inbound_message(conn, message_id, raw_text, context)
        except Exception:
            logger.exception("Background inbound processing failed for %s", message_id)

    threading.Thread(target=_run, daemon=True).start()


@inbound_bp.route("/sms", methods=["POST"])
def inbound_sms():
    """Africa's Talking inbound SMS webhook."""
    try:
        # AT sends form-encoded data
        phone = request.form.get("from") or request.values.get("from", "")
        text = (request.form.get("text") or request.values.get("text", "")).strip()

        if not text:
            return "", 200

        with get_connection() as conn:
            role, entity_id, property_id = _resolve_sender(conn, phone)
            message_id = generate_id("INB")
            conn.execute(
                """
                INSERT INTO inbound_messages
                (id, property_id, sender_phone, sender_role, sender_entity_id, raw_body, channel)
                VALUES (?, ?, ?, ?, ?, ?, 'sms')
                """,
                (message_id, property_id, phone, role, entity_id, text),
            )

        if property_id:
            _dispatch(message_id, text, {
                "property_id": property_id,
                "sender_role": role,
                "sender_entity_id": entity_id,
                "sender_phone": phone,
            })
        else:
            logger.info("Inbound SMS from %s — sender not resolved, message stored unprocessed", phone)

    except Exception:
        logger.exception("Error handling inbound SMS")

    return "", 200


@inbound_bp.route("/whatsapp", methods=["POST"])
def inbound_whatsapp():
    """WhatsApp inbound webhook. Stub — full payload parsing wired when credentials are live."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        phone = body.get("from") or body.get("sender", "")
        msg = body.get("message") or {}
        text = (body.get("text") or msg.get("text", "")).strip()

        if not text:
            return "", 200

        with get_connection() as conn:
            role, entity_id, property_id = _resolve_sender(conn, phone)
            message_id = generate_id("INB")
            conn.execute(
                """
                INSERT INTO inbound_messages
                (id, property_id, sender_phone, sender_role, sender_entity_id, raw_body, channel)
                VALUES (?, ?, ?, ?, ?, ?, 'whatsapp')
                """,
                (message_id, property_id, phone, role, entity_id, text),
            )

        if property_id:
            _dispatch(message_id, text, {
                "property_id": property_id,
                "sender_role": role,
                "sender_entity_id": entity_id,
                "sender_phone": phone,
            })

    except Exception:
        logger.exception("Error handling inbound WhatsApp message")

    return "", 200
