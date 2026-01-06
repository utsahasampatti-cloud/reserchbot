# services/openai_vision.py
# Production-safe OpenAI Vision + resale assistant (never crashes)
# Uses OpenAI Responses API with image input.
# Docs: https://platform.openai.com/docs/api-reference/responses  (Responses API)
#       https://platform.openai.com/docs/guides/images-vision     (Vision guide)

import os
import json
import base64
from typing import Dict, Any, Optional

from openai import OpenAI


def _b64_data_url(img_bytes: bytes, filename: Optional[str]) -> str:
    # Very small heuristic for mime type from extension; default png.
    ext = (os.path.splitext(filename or "")[1] or "").lower()
    mime = "image/png"
    if ext in [".jpg", ".jpeg"]:
        mime = "image/jpeg"
    elif ext == ".webp":
        mime = "image/webp"

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _safe_fallback(message: str) -> Dict[str, Any]:
    # Never crash. Always return expected structure.
    return {
        "item": {
            "name": "Unknown item",
            "brand": "",
            "model": "",
            "condition": "unknown",
        },
        "market_estimate": {
            "resale_price_range_usd": [0, 0],
            "confidence": "low",
        },
        "deal_analysis": {
            "verdict": "UNKNOWN",
            "risk_level": "high",
        },
        "assistant_message": message,
    }


def describe_item(img_bytes: bytes, filename: str | None = None, hint: str = "") -> Dict[str, Any]:
    """
    Returns a dict:
      {
        "item": {name, brand, model, condition},
        "market_estimate": {resale_price_range_usd:[low,high], confidence},
        "deal_analysis": {verdict, risk_level},
        "assistant_message": "..."
      }
    This function MUST NEVER raise (so FastAPI never 500s due to uncaught exceptions).
    """

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _safe_fallback("OPENAI_API_KEY is not set on the server. Add it in Railway Variables and redeploy.")

    # You can change model via Railway variable if needed.
    # Pick a multimodal-capable model.
    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()  # you can override in Railway

    client = OpenAI(api_key=api_key)

    data_url = _b64_data_url(img_bytes, filename)

    # System instruction: act as flea-market resale assistant.
    system_instructions = """
You are a resale assistant for flea markets in Europe.
You analyze the photo + user's hint and output STRICT JSON ONLY.

Your job:
1) Identify the item from the image as best as possible.
2) Estimate realistic resale price range in USD (low-high) based on typical online market behavior (not exact live eBay).
3) Give a BUY / BUY IF NEGOTIATED LOWER / SKIP verdict and risk_level (low/medium/high).
4) Keep it practical and short.

Output MUST be valid JSON with EXACT keys:
{
  "item": {"name": "...", "brand": "...", "model": "...", "condition": "..."},
  "market_estimate": {"resale_price_range_usd": [low, high], "confidence": "low|medium|high"},
  "deal_analysis": {"verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP", "risk_level": "low|medium|high"},
  "assistant_message": "one short paragraph"
}

Rules:
- resale_price_range_usd must be two integers (or floats) where low <= high
- If uncertain, keep confidence low and widen the range.
- No markdown. No extra keys. JSON ONLY.
""".strip()

    user_text = f"Hint from user: {hint}".strip()

    try:
        # Responses API with image input
        # See: Responses API reference + vision guide. :contentReference[oaicite:1]{index=1}
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
            # keep it cheap; adjust if needed
            max_output_tokens=450,
        )

        # The SDK exposes a convenient aggregate text:
        # Quickstart shows .output_text usage. :contentReference[oaicite:2]{index=2}
        raw = (resp.output_text or "").strip()
        if not raw:
            return _safe_fallback("AI returned empty output. Try again with a clearer photo and a short hint (brand/model).")

        # Parse strict JSON
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Sometimes the model can output extra text; attempt to extract JSON object
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(raw[start : end + 1])
                except Exception:
                    return _safe_fallback("Could not parse AI JSON output. Try again with a clearer photo (front/back labels).")
            else:
                return _safe_fallback("Could not parse AI JSON output. Try again with a clearer photo (front/back labels).")

        # Validate minimal structure
        item = data.get("item") or {}
        market = data.get("market_estimate") or {}
        deal = data.get("deal_analysis") or {}

        pr = market.get("resale_price_range_usd")
        if not (isinstance(pr, list) and len(pr) == 2):
            market["resale_price_range_usd"] = [0, 0]

        # Ensure numeric + ordered
        try:
            low = float(market["resale_price_range_usd"][0])
            high = float(market["resale_price_range_usd"][1])
            if high < low:
                low, high = high, low
            market["resale_price_range_usd"] = [round(low, 2), round(high, 2)]
        except Exception:
            market["resale_price_range_usd"] = [0, 0]

        # Clamp/normalize confidence/risk/verdict
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
        allowed = {"BUY", "BUY IF NEGOTIATED LOWER", "SKIP"}
        if verdict not in allowed:
            deal["verdict"] = "BUY IF NEGOTIATED LOWER"
        else:
            deal["verdict"] = verdict

        # Fill missing strings
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
        return _safe_fallback(f"OpenAI error: {str(e)}")
