# services/openai_vision.py
# Stable OpenAI Vision (timeout + server-side image resize) — prevents Railway 502
import os
import json
import base64
from io import BytesIO
from typing import Dict, Any, Optional

from openai import OpenAI
from PIL import Image


def _safe_fallback(message: str) -> Dict[str, Any]:
    return {
        "item": {"name": "Unknown item", "brand": "", "model": "", "condition": "unknown"},
        "market_estimate": {"resale_price_range_usd": [0, 0], "confidence": "low"},
        "deal_analysis": {"verdict": "UNKNOWN", "risk_level": "high"},
        "assistant_message": message,
    }


def _resize_for_api(img_bytes: bytes, max_side: int = 1024, jpeg_quality: int = 75) -> bytes:
    """
    Shrinks big images to reduce base64 payload and speed up OpenAI call.
    Returns JPEG bytes.
    """
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        m = max(w, h)
        if m > max_side:
            scale = max_side / float(m)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            img = img.resize(new_size)

        out = BytesIO()
        img.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
        return out.getvalue()
    except Exception:
        # If anything fails, keep original bytes
        return img_bytes


def _b64_data_url(img_bytes: bytes) -> str:
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def describe_item(img_bytes: bytes, filename: str | None = None, hint: str = "") -> Dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _safe_fallback("OPENAI_API_KEY is not set on the server (Railway Variables).")

    # Multimodal model
    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()

    # HARD TIMEOUTS to avoid Railway 502
    client = OpenAI(
        api_key=api_key,
        timeout=20.0,   # <- critical
        max_retries=1
    )

    # Resize to keep payload small and response fast
    small_bytes = _resize_for_api(img_bytes, max_side=1024, jpeg_quality=75)
    data_url = _b64_data_url(small_bytes)

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
- resale_price_range_usd must be two numbers where low <= high
- if uncertain: confidence low and widen range
- JSON ONLY. No markdown. No extra text.
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
            max_output_tokens=450,
        )

        raw = (resp.output_text or "").strip()
        if not raw:
            return _safe_fallback("AI returned empty output. Try a clearer photo and a short hint (brand/model).")

        # Parse JSON (with rescue extraction)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(raw[start:end + 1])
                except Exception:
                    return _safe_fallback("Could not parse AI JSON output. Try again with clearer photo.")
            else:
                return _safe_fallback("Could not parse AI JSON output. Try again with clearer photo.")

        item = data.get("item") or {}
        market = data.get("market_estimate") or {}
        deal = data.get("deal_analysis") or {}

        pr = market.get("resale_price_range_usd")
        if not (isinstance(pr, list) and len(pr) == 2):
            market["resale_price_range_usd"] = [0, 0]

        # normalize numeric
        try:
            low = float(market["resale_price_range_usd"][0])
            high = float(market["resale_price_range_usd"][1])
            if high < low:
                low, high = high, low
            market["resale_price_range_usd"] = [round(low, 2), round(high, 2)]
        except Exception:
            market["resale_price_range_usd"] = [0, 0]

        conf = str(market.get("confidence", "low")).lower()
        if conf not in ["low", "medium", "high"]:
            market["confidence"] = "low"
        else:
            market["confidence"] = conf

        risk = str(deal.get("risk_level", "medium")).lower()
        if risk not in ["low", "medium", "high"]:
            deal["risk_level"] = "medium"
        else:
            deal["risk_level"] = risk

        verdict = str(deal.get("verdict", "BUY IF NEGOTIATED LOWER")).upper()
        if verdict not in {"BUY", "BUY IF NEGOTIATED LOWER", "SKIP"}:
            deal["verdict"] = "BUY IF NEGOTIATED LOWER"
        else:
            deal["verdict"] = verdict

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
        return _safe_fallback(f"OpenAI/vision error (timeout-safe): {str(e)}")
