# services/openai_vision.py
# SAFE MVP VERSION — NEVER CRASHES
# Python 3.13 compatible

from typing import Dict, Any


def describe_item(
    img_bytes: bytes,
    filename: str | None = None,
    hint: str = ""
) -> Dict[str, Any]:
    """
    SAFE placeholder vision agent.
    Always returns valid structured output for UI rendering.
    Does NOT call external APIs (OpenAI) yet.
    """

    # Minimal heuristic (only for demo stability)
    name = "Unknown item"
    brand = ""
    model = ""

    text_hint = (hint or "").lower()

    if "iphone" in text_hint:
        name = "iPhone"
        brand = "Apple"
        model = "iPhone (exact model unknown)"

    return {
        "item": {
            "name": name,
            "brand": brand,
            "model": model,
            "condition": "visible condition cannot be reliably determined from image alone"
        },
        "market_estimate": {
            "resale_price_range_usd": [0, 0],
            "confidence": "low"
        },
        "deal_analysis": {
            "verdict": "UNKNOWN",
            "risk_level": "high"
        },
        "assistant_message": (
            "Image received successfully. "
            "AI vision analysis is temporarily disabled for stability. "
            "You can still test the full user flow."
        )
    }
