"""Conversation session state helpers for inbound channels."""

import json
from datetime import datetime, timedelta


def get_session_state(conn, phone):
    row = conn.execute(
        """
        SELECT phone, property_id, last_intent, awaiting_confirmation, context_json, expires_at
        FROM inbound_sessions
        WHERE phone = ?
        """,
        (phone,),
    ).fetchone()
    if not row:
        return None
    state = dict(row)
    if state.get("context_json"):
        try:
            state["context"] = json.loads(state["context_json"])
        except Exception:
            state["context"] = {}
    else:
        state["context"] = {}
    return state


def upsert_session_state(conn, phone, property_id, last_intent=None, awaiting_confirmation=None, context=None):
    expires_at = (datetime.utcnow() + timedelta(hours=24)).isoformat(sep=" ")
    context_json = json.dumps(context or {})
    conn.execute(
        """
        INSERT INTO inbound_sessions (phone, property_id, last_intent, awaiting_confirmation, context_json, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            property_id = excluded.property_id,
            last_intent = excluded.last_intent,
            awaiting_confirmation = excluded.awaiting_confirmation,
            context_json = excluded.context_json,
            expires_at = excluded.expires_at
        """,
        (phone, property_id, last_intent, awaiting_confirmation, context_json, expires_at),
    )

