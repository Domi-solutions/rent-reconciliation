"""Inbound intent classification and action handlers."""


def classify_intent(raw_text, sender_role="unknown"):
    """Phase D placeholder intent classifier."""
    text = (raw_text or "").strip().lower()
    if text in {"yes", "no", "skip"}:
        return {"intent": "confirmation", "confidence": 0.95, "extracted": {"value": text}}
    return {"intent": "unknown", "confidence": 0.5, "extracted": {}}

