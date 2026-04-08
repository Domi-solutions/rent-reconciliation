"""Outbound response helpers for inbound workflows."""


def build_response_text(intent_result):
    """Phase D placeholder responder."""
    intent = (intent_result or {}).get("intent", "unknown")
    if intent == "confirmation":
        return "Confirmation captured."
    return "Message received. Action preview pending."

