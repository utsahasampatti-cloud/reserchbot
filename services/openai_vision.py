from __future__ import annotations

import base64
import os
from typing import Any, Dict, List

from openai import OpenAI


SYSTEM_PROMPT = """
You are a street-smart resale assistant.
Return ONLY valid JSON.
Be conservative. If unsure, widen ranges and set confidence low.
Never promise profit.
"""

USER_PROMPT = """
Analyze the item in the photo(s). Identify: name, brand, model if possible, and visible condition.

Then produce a conservative resale price range in USD for online resale (rough estimate).
Confidence: low/medium/high.

Return JSON exactly in this shape:
{
  "item": {"name":": "...", "brand": "...", "model": "...", "condition": "..."},
  "market_estimate": {"resale_price_range_usd": [low, high], "confidence": "low|medium|high"},
  "risk_level": "low|medium|high",
  "notes": ["..."]
}
"""


def _b64_data_url(image_bytes: bytes, content_type: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{content_type};base64,{b64}"


def build_queries(item: Dict[str, Any], hint: str = "") -> List[str]:
    name = (item.get("name") or "").strip()
    brand = (item.get("brand") or "").strip()
    model = (item.get("model") or "").strip()
    base = " ".join([brand, name, model]).strip()
    hint = (hint or "").strip()

    q1 = " ".join([base, "used"]).strip()
    q2 = " ".join([base, hint]).strip()
    q3 = " ".join([brand, name, "unlocked"]).strip()

    queries = []
    for q in [q1, q2, q3]:
        q = " ".join(q.split())
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)
    return queries[:3]


def vision_quick_sniff(images: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    images: [{"data_url": "...", "filename": "..."}]
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=api_key)

    content = [{"type": "input_text", "text": USER_PROMPT}]
    for img in images[:5]:
        content.append({"type": "input_image", "image_url": img["data_url"]})

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )

    text = resp.output_text.strip()
    # model returns JSON text — parse defensively in main.py
    return {"raw_json_text": text}
