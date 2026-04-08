"""Thin LLM wrapper for agent workflows.

This is the only module that should import the Anthropic SDK.
"""

import os


def call_llm(prompt, model="fast"):
    """Call Anthropic model via a stable wrapper."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    try:
        from anthropic import Anthropic
    except Exception:
        return ""

    model_map = {
        "fast": "claude-haiku-4-5-20251001",
        "smart": "claude-sonnet-4-6",
    }
    resolved_model = model_map.get(model, model_map["fast"])

    try:
        client = Anthropic(api_key=api_key)
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

