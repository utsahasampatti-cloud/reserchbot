# services/openai_vision.py
# Stable OpenAI Vision WITHOUT pillow (prevents build/runtime issues)
# Adds strict timeout to avoid Railway 502
import os
import json
import base64
from typing import Dict, Any, Optional

from openai import OpenAI


def _safe_fallback(message: str) -> Dict[str, Any]:
    return {
        "item": {"name": "Unknown item", "brand": "", "model": "", "condition": "unknown"},
        "market_estimate": {"resale_price_range_usd": [0, 0], "confidence": "low"},
        "deal_analysis": {"verdict": "UNKNOWN", "risk_level": "high"},
        "assistant_message": message,
    }


def _mime_from_filename(filename: Optional[str]) -> str:
    ext = (os.path.splitext(filename or "")[1] or "").lower()
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"


def _b64_data_url(img_bytes: bytes, filename: Optional[str]) -> str:
    mime = _mime_from_filename(filename)
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def describe_item(img_bytes: bytes, filename: str | None = None, hint: str = "") -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _safe_fallback("OPENAI_API_KEY is not set on the server (Railway Variables).")

    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()

    # HARD TIMEOUTS -> no hanging -> fewer Railway 502
    client = OpenAI(api_key=api_key, timeout=20.0, max_retries=1)

    data_url = _b64_data_url(img_bytes, filename)

    system_instructions = """
You are a resale assistant for flea markets in Europe.
Analyze the photo + hint and output STRICT JSON ONLY.

Return EXACT JSON keys:
{
  "item": {"name": "...", "brand": "...", "model": "...", "condition": "..."},
  "market_estimate": {"resale_price_range_usd": [low, high], "confidence": "low|medium|high"},
  "deal_analysis": {"verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP", "risk_level": "low|medium|high"},
  "assistant_message": "one short paragraph"
}

Rules:
- JSON ONLY. No markdown. No extra text.
- resale_price_range_usd: two numbers, low <= high.
- If uncertain: confidence low and widen range.
""".strip()

    user_text = f"Hint from user: {hint}".strip()

    try:
        resp = client.responses.create(
            model=model,
            instructions=system_instructions,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_text},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            max_output_tokens=350,
        )

        raw = (resp.output_text or "").strip()
        if not raw:
            return _safe_fallback("AI returned empty output. Try again with clearer photo + hint.")

        # strict JSON parse (+ rescue)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            s, e = raw.find("{"), raw.rfind("}")
            if s != -1 and e != -1 and e > s:
                try:
                    data = json.loads(raw[s : e + 1])
                except Exception:
                    return _safe_fallback("Could not parse AI JSON output. Try clearer photo.")
            else:
                return _safe_fallback("Could not parse AI JSON output. Try clearer photo.")

        item = data.get("item") or {}
        market = data.get("market_estimate") or {}
        deal = data.get("deal_analysis") or {}

        pr = market.get("resale_price_range_usd")
        if not (isinstance(pr, list) and len(pr) == 2):
            market["resale_price_range_usd"] = [0, 0]

        # normalize
        try:
            low = float(market["resale_price_range_usd"][0])
            high = float(market["resale_price_range_usd"][1])
            if high < low:
                low, high = high, low
            market["resale_price_range_usd"] = [round(low, 2), round(high, 2)]
        except Exception:
            market["resale_price_range_usd"] = [0, 0]

        conf = str(market.get("confidence", "low")).lower()
        market["confidence"] = conf if conf in ["low", "medium", "high"] else "low"

        risk = str(deal.get("risk_level", "medium")).lower()
        deal["risk_level"] = risk if risk in ["low", "medium", "high"] else "medium"

        verdict = str(deal.get("verdict", "BUY IF NEGOTIATED LOWER")).upper()
        deal["verdict"] = verdict if verdict in {"BUY", "BUY IF NEGOTIATED LOWER", "SKIP"} else "BUY IF NEGOTIATED LOWER"

        item.setdefault("name", "Unknown item")
        item.setdefault("brand", "")
        item.setdefault("model", "")
        item.setdefault("condition", "unknown")

        msg = data.get("assistant_message")
        if not isinstance(msg, str) or not msg.strip():
            msg = "AI analysis completed."

        return {
            "item": item,
            "market_estimate": market,
            "deal_analysis": deal,
            "assistant_message": msg.strip(),
        }

    except Exception as e:
        return _safe_fallback(f"OpenAI error (timeout-safe): {str(e)}")
