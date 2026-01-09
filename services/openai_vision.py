# services/openai_vision.py
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


def _b64_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip().replace(",", ".")
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


SYSTEM_PROMPT = """Act as a personal resale assistant for European flea markets and second-hand finds.
Be conservative. Protect the user's money and time.

Return ONLY valid JSON in the exact structure:

{
  "item": {"name":"...","brand":"...","model":"...","condition":"..."},
  "market_estimate": {"resale_price_range_usd":[low,high],"confidence":"low|medium|high"},
  "deal_analysis": {"verdict":"BUY|BUY IF NEGOTIATED LOWER|SKIP","risk_level":"low|medium|high"},
  "assistant_message":"..."
}

Rules:
- If you cannot reliably identify the item, set confidence='low' and widen the range.
- Never recommend BUY unless margin is clearly profitable and risks manageable.
- Keep assistant_message short, direct, street-smart. No emojis.
"""


def call_vision_pricing(
    images: List[Tuple[bytes, str]],  # list of (bytes, mime)
    hint: Optional[str] = None,
    asking_price: Optional[float] = None,
    currency: str = "USD",
    language: str = "en",
    mode: str = "quick",
    timeout_sec: int = 45,
) -> Dict[str, Any]:
    """
    images: list of (image_bytes, mime)
    mode: quick|deep (deep = slightly longer instructions, still same schema)
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing")

    client = OpenAI(api_key=api_key)

    user_context = {
        "hint": hint or "",
        "asking_price": asking_price,
        "currency": currency,
        "language": language,
        "mode": mode,
        "notes": "User may provide multiple photos of the same item from different angles. Use all of them.",
    }

    if mode == "deep":
        extra = (
            "DEEP MODE: use multiple photos to reduce uncertainty; "
            "if still uncertain, explicitly say so and widen the range."
        )
    else:
        extra = "QUICK MODE: keep it fast, conservative."

    # Build multi-modal content: text + many images
    content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"{extra}\n"
                f"User context: {user_context}\n\n"
                "Now analyze the item from the photos and return the required JSON."
            ),
        }
    ]

    for (img_bytes, mime) in images[:8]:  # cap to 8 images
        content.append(
            {"type": "image_url", "image_url": {"url": _b64_data_url(img_bytes, mime)}}
        )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        timeout=timeout_sec,
    )

    txt = resp.choices[0].message.content or "{}"

    # txt is JSON string already (response_format=json_object), but keep safe:
    import json

    data = json.loads(txt)

    # Normalize + guard
    low_high = data.get("market_estimate", {}).get("resale_price_range_usd", [0, 0])
    if not isinstance(low_high, list) or len(low_high) != 2:
        low_high = [0, 0]

    low = _safe_float(low_high[0]) or 0.0
    high = _safe_float(low_high[1]) or 0.0
    if high < low:
        low, high = high, low

    data.setdefault("item", {})
    data.setdefault("market_estimate", {})
    data.setdefault("deal_analysis", {})
    data.setdefault("assistant_message", "")

    data["market_estimate"]["resale_price_range_usd"] = [round(low, 2), round(high, 2)]

    return data
