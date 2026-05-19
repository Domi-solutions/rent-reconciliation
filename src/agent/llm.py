"""Thin LLM wrapper for agent workflows.

This is the only module that should import the Anthropic SDK.
"""

import os


def _get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except Exception:
        return None


def call_llm(prompt, model="fast"):
    """Call Anthropic model via a stable wrapper."""
    client = _get_client()
    if not client:
        return ""

    model_map = {
        "fast": "claude-haiku-4-5-20251001",
        "smart": "claude-sonnet-4-6",
    }
    resolved_model = model_map.get(model, model_map["fast"])

    try:
        response = client.messages.create(
            model=resolved_model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        if response and response.content:
            return "".join(block.text for block in response.content if hasattr(block, "text")).strip()
    except Exception:
        return ""

    return ""


def call_llm_vision(pages_b64: list[str], prompt: str, model: str = "fast", max_tokens: int = 4096) -> str:
    """
    Call Anthropic vision model with one or more base64-encoded PNG page images.

    pages_b64: list of base64-encoded PNG strings (one per PDF page)
    prompt: text instruction appended after the images
    Returns the model's text response, or "" on failure.
    """
    client = _get_client()
    if not client:
        return ""

    model_map = {
        "fast": "claude-haiku-4-5-20251001",
        "smart": "claude-sonnet-4-6",
    }
    resolved_model = model_map.get(model, model_map["fast"])

    content = []
    for b64 in pages_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({"type": "text", "text": prompt})

    try:
        response = client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        if response and response.content:
            return "".join(block.text for block in response.content if hasattr(block, "text")).strip()
    except Exception:
        return ""

    return ""

