"""Inbound intent classification and action handlers."""

import re
from datetime import datetime

from src.agent.llm import call_llm
from src.agent.state import get_session_state, upsert_session_state
from src.database.db import generate_id
from src.parsers.sms_parser import parse_mpesa_message


INTENTS = {
    "maintenance_report",
    "maintenance_resolve",
    "payment_claim",
    "followup_note",
    "query_balance",
    "query_arrears",
    "checkin_reply",
    "owner_instruction",
    "occupancy_update",
    "confirmation",
    "unknown",
}


def _extract_unit_number(text):
    match = re.search(r"\b([A-Za-z]\d{1,3})\b", text or "")
    return match.group(1).upper() if match else None


def classify_intent(raw_text, sender_role="unknown"):
    """Classify inbound text into one supported intent."""
    text = (raw_text or "").strip()
    lower = text.lower()

    if lower in {"yes", "no", "skip"}:
        return {"intent": "confirmation", "confidence": 0.98, "extracted": {"value": lower}}
    if lower in {"1", "2", "3"}:
        return {"intent": "checkin_reply", "confidence": 0.97, "extracted": {"numeric_response": int(lower)}}

    parsed_sms = parse_mpesa_message(text)
    if parsed_sms.get("mpesa_ref"):
        return {
            "intent": "payment_claim",
            "confidence": 0.98,
            "extracted": {
                "mpesa_ref": parsed_sms.get("mpesa_ref"),
                "amount": float(parsed_sms.get("amount") or 0),
                "raw_message": text,
            },
        }
    ref_match = re.search(r"\b([A-Z0-9]{8,12})\b", (text or "").upper())
    amount_match = re.search(r"(?:KSH|KES)\s*([0-9,]+(?:\.[0-9]{1,2})?)", (text or "").upper())
    if ref_match and amount_match:
        amount_value = float(amount_match.group(1).replace(",", ""))
        return {
            "intent": "payment_claim",
            "confidence": 0.9,
            "extracted": {
                "mpesa_ref": ref_match.group(1),
                "amount": amount_value,
                "raw_message": text,
            },
        }

    if any(k in lower for k in {"tap", "leak", "broken", "maintenance", "plumbing", "electricity"}):
        return {
            "intent": "maintenance_report",
            "confidence": 0.9,
            "extracted": {"unit_number": _extract_unit_number(text)},
        }
    if any(k in lower for k in {"fixed", "resolved", "done", "complete"}):
        return {
            "intent": "maintenance_resolve",
            "confidence": 0.88,
            "extracted": {"unit_number": _extract_unit_number(text)},
        }
    if any(k in lower for k in {"how much", "balance", "owe"}):
        return {
            "intent": "query_balance",
            "confidence": 0.9,
            "extracted": {"unit_number": _extract_unit_number(text)},
        }
    if any(k in lower for k in {"arrears", "who has not paid", "which units"}):
        return {"intent": "query_arrears", "confidence": 0.9, "extracted": {}}
    if any(k in lower for k in {"follow up", "follow-up", "will pay", "promised"}):
        return {
            "intent": "followup_note",
            "confidence": 0.88,
            "extracted": {"unit_number": _extract_unit_number(text)},
        }
    if sender_role == "owner" and any(k in lower for k in {"focus", "prioritize", "urgent", "instruction"}):
        return {"intent": "owner_instruction", "confidence": 0.9, "extracted": {}}
    if any(k in lower for k in {"new tenant", "vacant", "occupied", "moved in", "moved out"}):
        return {
            "intent": "occupancy_update",
            "confidence": 0.88,
            "extracted": {"unit_number": _extract_unit_number(text)},
        }

    # LLM fallback for ambiguous text.
    llm_prompt = (
        "Classify this property-management inbound message into exactly one intent from: "
        "maintenance_report, maintenance_resolve, payment_claim, followup_note, query_balance, "
        "query_arrears, checkin_reply, owner_instruction, occupancy_update, confirmation, unknown. "
        "Return only: intent|confidence where confidence is 0-1.\n"
        f"role={sender_role}\nmessage={text}"
    )
    llm_raw = call_llm(llm_prompt, model="fast")
    if "|" in llm_raw:
        intent_raw, conf_raw = llm_raw.split("|", 1)
        intent = intent_raw.strip()
        if intent in INTENTS:
            try:
                confidence = float(conf_raw.strip())
            except Exception:
                confidence = 0.5
            return {"intent": intent, "confidence": max(0.0, min(confidence, 1.0)), "extracted": {}}

    return {"intent": "unknown", "confidence": 0.5, "extracted": {}}


def _get_unit_for_context(conn, property_id, sender_entity_id=None, extracted_unit_number=None):
    if extracted_unit_number:
        unit = conn.execute(
            "SELECT id, unit_number FROM units WHERE property_id = ? AND unit_number = ?",
            (property_id, extracted_unit_number),
        ).fetchone()
        if unit:
            return unit
    if sender_entity_id:
        tenant_unit = conn.execute(
            "SELECT u.id, u.unit_number FROM units u JOIN tenants t ON t.unit_id = u.id WHERE t.id = ?",
            (sender_entity_id,),
        ).fetchone()
        if tenant_unit:
            return tenant_unit
    return None


def _handle_payment_claim(conn, message_id, raw_text, context, extracted):
    unit = _get_unit_for_context(
        conn,
        context["property_id"],
        sender_entity_id=context.get("sender_entity_id"),
        extracted_unit_number=extracted.get("unit_number"),
    )
    if not unit:
        return "Payment claim received. Unit could not be resolved.", "payment_claim_unresolved"

    claim_id = generate_id("CLM")
    conn.execute(
        """
        INSERT INTO payment_claims
        (id, property_id, mpesa_ref, unit_id, claimed_amount, raw_message, source, status)
        VALUES (?, ?, ?, ?, ?, ?, 'inbound', 'pending')
        """,
        (
            claim_id,
            context["property_id"],
            extracted.get("mpesa_ref", ""),
            unit["id"],
            extracted.get("amount"),
            raw_text,
        ),
    )
    return "Payment claim received and logged as pending.", f"payment_claim_created:{claim_id}"


def _handle_maintenance_report(conn, raw_text, context, extracted):
    unit = _get_unit_for_context(
        conn,
        context["property_id"],
        sender_entity_id=context.get("sender_entity_id"),
        extracted_unit_number=extracted.get("unit_number"),
    )
    issue_id = generate_id("ISSUE")
    conn.execute(
        """
        INSERT INTO maintenance_issues
        (id, property_id, unit_id, source, raised_by_tenant_id, category, title, description, status)
        VALUES (?, ?, ?, ?, ?, 'general', ?, ?, 'open')
        """,
        (
            issue_id,
            context["property_id"],
            unit["id"] if unit else None,
            context.get("sender_role", "unknown"),
            context.get("sender_entity_id") if context.get("sender_role") == "tenant" else None,
            "Issue reported via inbound message",
            raw_text,
        ),
    )
    return "Maintenance issue logged for follow-up.", f"maintenance_issue_created:{issue_id}"


def _handle_maintenance_resolve(conn, raw_text, context, extracted):
    unit = _get_unit_for_context(
        conn,
        context["property_id"],
        extracted_unit_number=extracted.get("unit_number"),
    )
    if not unit:
        return "Resolution note received. Unit could not be resolved.", "maintenance_resolve_unresolved"
    issue = conn.execute(
        """
        SELECT id FROM maintenance_issues
        WHERE property_id = ? AND unit_id = ? AND status = 'open'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (context["property_id"], unit["id"]),
    ).fetchone()
    if not issue:
        return "Resolution noted. No open issue found for that unit.", "maintenance_resolve_no_open_issue"

    conn.execute(
        """
        UPDATE maintenance_issues
        SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP, resolved_note = ?
        WHERE id = ?
        """,
        (raw_text, issue["id"]),
    )
    return "Maintenance issue marked resolved.", f"maintenance_issue_resolved:{issue['id']}"


def _handle_query_balance(conn, context, extracted):
    unit = _get_unit_for_context(
        conn,
        context["property_id"],
        sender_entity_id=context.get("sender_entity_id"),
        extracted_unit_number=extracted.get("unit_number"),
    )
    if not unit:
        return "Balance request received. Unit could not be resolved.", "balance_query_unresolved"
    row = conn.execute(
        """
        SELECT balance
        FROM unit_balances
        WHERE property_id = ? AND unit_id = ?
        """,
        (context["property_id"], unit["id"]),
    ).fetchone()
    balance = float(row["balance"] if row else 0)
    return f"Unit {unit['unit_number']} current balance: KES {balance:,.0f}.", f"balance_query:{unit['id']}"


def _handle_query_arrears(conn, context):
    row = conn.execute(
        """
        SELECT COUNT(*) AS units, COALESCE(SUM(balance), 0) AS total
        FROM unit_balances
        WHERE property_id = ? AND balance > 0
        """,
        (context["property_id"],),
    ).fetchone()
    return (
        f"Arrears summary: {row['units']} unit(s), KES {float(row['total']):,.0f} outstanding.",
        "arrears_query_summary",
    )


def _handle_followup_note(conn, raw_text, context, extracted):
    unit = _get_unit_for_context(
        conn,
        context["property_id"],
        extracted_unit_number=extracted.get("unit_number"),
    )
    conn.execute(
        """
        INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)
        VALUES ('followup_note', 'unit', ?, ?, 'agent')
        """,
        (unit["id"] if unit else None, raw_text),
    )
    return "Follow-up note logged.", "followup_note_logged"


def _handle_owner_instruction(conn, raw_text, context):
    conn.execute(
        """
        INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)
        VALUES ('owner_instruction', 'property', ?, ?, 'agent')
        """,
        (context["property_id"], raw_text),
    )
    return "Instruction logged and queued for follow-up.", "owner_instruction_logged"


def _handle_occupancy_update(conn, raw_text, context, extracted):
    unit = _get_unit_for_context(
        conn,
        context["property_id"],
        extracted_unit_number=extracted.get("unit_number"),
    )
    if not unit:
        return "Occupancy update received. Unit could not be resolved.", "occupancy_update_unresolved"
    status = "occupied" if "occupied" in raw_text.lower() or "new tenant" in raw_text.lower() or "moved in" in raw_text.lower() else "vacant"
    conn.execute(
        "UPDATE units SET status = ?, status_changed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, unit["id"]),
    )
    return f"Unit {unit['unit_number']} status updated to {status}.", f"occupancy_updated:{unit['id']}"


def _handle_checkin_reply(conn, raw_text, context, extracted):
    conn.execute(
        """
        INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)
        VALUES ('checkin_reply', 'tenant', ?, ?, 'agent')
        """,
        (context.get("sender_entity_id"), raw_text),
    )
    return "Check-in response recorded.", "checkin_reply_logged"


def _handle_confirmation(conn, raw_text, context):
    state = get_session_state(conn, context.get("sender_phone"))
    awaiting = state.get("awaiting_confirmation") if state else None
    if not awaiting:
        return "Confirmation received. No pending action found.", "confirmation_without_pending"
    conn.execute(
        """
        INSERT INTO audit_log (action, entity_type, entity_id, details, user_id)
        VALUES ('confirmation', 'session', ?, ?, 'agent')
        """,
        (context.get("sender_phone"), f"{awaiting} -> {raw_text}"),
    )
    upsert_session_state(
        conn,
        phone=context.get("sender_phone"),
        property_id=context.get("property_id"),
        last_intent="confirmation",
        awaiting_confirmation=None,
        context={},
    )
    return "Confirmation captured for pending action.", "confirmation_applied"


def process_inbound_message(conn, message_id, raw_text, context):
    """Classify, act, and update inbound_messages record."""
    classification = classify_intent(raw_text, sender_role=context.get("sender_role", "unknown"))
    intent = classification["intent"]
    extracted = classification.get("extracted", {})
    confidence = float(classification.get("confidence", 0))

    if confidence < 0.85 and intent not in {"confirmation", "checkin_reply"}:
        response = "Message received. Reply YES to proceed or provide more detail."
        action_taken = "awaiting_confirmation"
        upsert_session_state(
            conn,
            phone=context.get("sender_phone"),
            property_id=context.get("property_id"),
            last_intent=intent,
            awaiting_confirmation=intent,
            context={"raw_text": raw_text, "extracted": extracted},
        )
    else:
        handlers = {
            "payment_claim": lambda: _handle_payment_claim(conn, message_id, raw_text, context, extracted),
            "maintenance_report": lambda: _handle_maintenance_report(conn, raw_text, context, extracted),
            "maintenance_resolve": lambda: _handle_maintenance_resolve(conn, raw_text, context, extracted),
            "query_balance": lambda: _handle_query_balance(conn, context, extracted),
            "query_arrears": lambda: _handle_query_arrears(conn, context),
            "followup_note": lambda: _handle_followup_note(conn, raw_text, context, extracted),
            "owner_instruction": lambda: _handle_owner_instruction(conn, raw_text, context),
            "occupancy_update": lambda: _handle_occupancy_update(conn, raw_text, context, extracted),
            "checkin_reply": lambda: _handle_checkin_reply(conn, raw_text, context, extracted),
            "confirmation": lambda: _handle_confirmation(conn, raw_text, context),
        }
        if intent in handlers:
            response, action_taken = handlers[intent]()
        else:
            response, action_taken = ("Message received. Please share more detail.", "unknown_intent")

    conn.execute(
        """
        UPDATE inbound_messages
        SET processed_at = CURRENT_TIMESTAMP,
            classified_intent = ?,
            confidence = ?,
            action_taken = ?,
            response_sent = ?
        WHERE id = ?
        """,
        (intent, confidence, action_taken, response, message_id),
    )
    return {
        "intent": intent,
        "confidence": confidence,
        "extracted": extracted,
        "action_taken": action_taken,
        "response": response,
    }

