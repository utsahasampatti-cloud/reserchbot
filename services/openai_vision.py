import os
import json
import base64
from typing import List, Optional, Dict, Any

from openai import OpenAI


def _b64_data_url(content_type: str, data: bytes) -> str:
    b64 = base64.b64encode(data).decode("utf-8")
    ct = content_type or "image/jpeg"
    return f"data:{ct};base64,{b64}"


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def analyze_with_openai(
    images: List[Dict[str, Any]],
    hint: Optional[str] = None,
    asking_price: Optional[float] = None,
    deep: bool = False,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns dict with:
    {
      "ui": { "fields": {...}, "summary": "..." }
    }
    Always keep it UI-safe.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {
            "ui": {
                "fields": {
                    "Item": "Temporary fallback",
                    "Condition": "unknown",
                    "Resale Price Range": "$0 — $0",
                    "Confidence": "low",
                    "Risk Level": "low",
                    "Verdict": "SKIP",
                },
                "summary": "OpenAI key is not configured (safe mode).",
            }
        }

    client = OpenAI(api_key=api_key)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Build vision messages
    content = []
    text_bits = []
    text_bits.append("You are a street-smart resale assistant for European flea markets.")
    text_bits.append("Analyze the photos and return ONLY valid JSON matching the schema.")
    if hint:
        text_bits.append(f"User hint: {hint}")
    if asking_price is not None:
        text_bits.append(f"Asking price (user typed): {asking_price} USD (treat as what seller asks).")

    # multi-image: attach all
    for img in images[:6]:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _b64_data_url(img.get("content_type") or "image/jpeg", img["bytes"])},
            }
        )

    content.insert(0, {"type": "text", "text": "\n".join(text_bits)})

    system_prompt = """
You are not a chatbot. You are a conservative, experienced reseller.
Return ONLY JSON. No markdown. No extra text.
If unsure, widen ranges and set low confidence.

OUTPUT JSON STRUCTURE:
{
  "item": { "name": "...", "brand": "unknown|...", "model": "unknown|...", "condition": "..." },
  "market_estimate": { "resale_price_range_usd": [low, high], "confidence": "low|medium|high" },
  "deal_analysis": { "verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP", "risk_level": "low|medium|high" },
  "assistant_message": "short practical advice"
}

Rules:
- conservative prices
- if asking_price provided: verdict must react to margin
"""

    # Ask OpenAI (chat.completions is stable)
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": content},
        ],
    )

    raw = resp.choices[0].message.content or "{}"

    # Parse JSON safely
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        # If model returned garbage, safe fallback
        return {
            "ui": {
                "fields": {
                    "Item": "Unknown (parse error)",
                    "Condition": "unknown",
                    "Resale Price Range": "$0 — $0",
                    "Confidence": "low",
                    "Risk Level": "high",
                    "Verdict": "SKIP",
                },
                "summary": "Could not parse AI response. Try again with clearer photo(s).",
            }
        }

    # Normalize to UI
    item = data.get("item") or {}
    market = data.get("market_estimate") or {}
    deal = data.get("deal_analysis") or {}

    rng = market.get("resale_price_range_usd") or [0, 0]
    try:
        low = float(rng[0])
        high = float(rng[1])
    except Exception:
        low, high = 0.0, 0.0

    verdict = (deal.get("verdict") or "SKIP").upper()
    if verdict not in ["BUY", "BUY IF NEGOTIATED LOWER", "SKIP"]:
        verdict = "SKIP"

    confidence = (market.get("confidence") or "low").lower()
    if confidence not in ["low", "medium", "high"]:
        confidence = "low"

    risk_level = (deal.get("risk_level") or "medium").lower()
    if risk_level not in ["low", "medium", "high"]:
        risk_level = "medium"

    name = item.get("name") or "Unknown item"
    condition = item.get("condition") or "unknown"

    # display range
    def fmt_money(x: float) -> str:
        if x.is_integer():
            return str(int(x))
        return f"{x:.0f}"

    price_str = f"${fmt_money(low)} — ${fmt_money(high)}"

    # summary text
    advice = data.get("assistant_message") or "—"

    # OPTIONAL: add Deep Research CTA hint via summary only (front can show a button)
    if confidence in ["low", "medium"] and not deep:
        advice = advice.strip() + " | Можеш натиснути “Копати глибше” і додати ще фото для точнішої ціни."

    return {
        "ui": {
            "fields": {
                "Item": name,
                "Condition": condition,
                "Resale Price Range": price_str,
                "Confidence": confidence,
                "Risk Level": risk_level,
                "Verdict": verdict,
            },
            "summary": advice,
        }
    }
