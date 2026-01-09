# services/openai_vision.py
import os
import base64
import json
from openai import OpenAI


def _b64(img: bytes) -> str:
    return base64.b64encode(img).decode("utf-8")


def vision_quick_sniff(openai_images: list[dict], hint: str | None = None) -> dict:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    system_prompt = """
You are a resale assistant for flea market finds.
Analyze the photos and return ONLY valid JSON.

Schema:
{
  "item": {
    "name": string,
    "brand": string,
    "model": string,
    "condition": string
  },
  "market_estimate": {
    "resale_price_range_usd": [number, number],
    "confidence": "low|medium|high"
  },
  "deal_analysis": {
    "verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP",
    "risk_level": "low|medium|high"
  },
  "assistant_message": string
}

Rules:
- Conservative estimates
- No hype
- If unsure → wide range + low confidence
"""

    content = [{"type": "text", "text": hint or "Analyze item for resale value"}]

    for img in openai_images:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{img.get('content_type','image/jpeg')};base64,{_b64(img['bytes'])}"
            }
        })

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content}
        ],
        temperature=0.2
    )

    raw = response.choices[0].message.content

    try:
        return json.loads(raw)
    except Exception:
        return {
            "item": {"name": "unknown", "brand": "unknown", "model": "unknown", "condition": "unknown"},
            "market_estimate": {"resale_price_range_usd": [0, 0], "confidence": "low"},
            "deal_analysis": {"verdict": "SKIP", "risk_level": "high"},
            "assistant_message": "Could not confidently analyze this item."
        }
