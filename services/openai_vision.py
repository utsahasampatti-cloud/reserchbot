import os
import json
import base64
from typing import Dict, Any, Optional, List
from openai import OpenAI

def _safe_fallback(message: str) -> Dict[str, Any]:
    return {
        "item": {"name": "Unknown item", "brand": "", "model": "", "condition": "unknown"},
        "market_estimate": {"resale_price_range_usd": [0, 0], "confidence": "low"},
        "deal_analysis": {"verdict": "BUY IF NEGOTIATED LOWER", "risk_level": "high"},
        "assistant_message": message,
    }

def _mime_from_filename(filename: Optional[str]) -> str:
    ext = (os.path.splitext(filename or "")[1] or "").lower()
    if ext in [".jpg", ".jpeg"]: return "image/jpeg"
    if ext == ".webp": return "image/webp"
    return "image/png"

def _b64_data_url(img_bytes: bytes, filename: Optional[str]) -> str:
    mime = _mime_from_filename(filename)
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"

def _parse_strict_json(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if not raw: return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e != -1 and e > s:
            try: return json.loads(raw[s:e+1])
            except Exception: return None
        return None

def _normalize_payload(data: dict) -> dict:
    item = data.get("item") or {}
    market = data.get("market_estimate") or {}
    deal = data.get("deal_analysis") or {}
    msg = data.get("assistant_message") or ""

    pr = market.get("resale_price_range_usd")
    if not (isinstance(pr, list) and len(pr) == 2):
        market["resale_price_range_usd"] = [0, 0]
    try:
        low = float(market["resale_price_range_usd"][0])
        high = float(market["resale_price_range_usd"][1])
        if high < low: low, high = high, low
        market["resale_price_range_usd"] = [round(low, 2), round(high, 2)]
    except Exception:
        market["resale_price_range_usd"] = [0, 0]

    conf = str(market.get("confidence", "low")).lower()
    market["confidence"] = conf if conf in ["low","medium","high"] else "low"

    risk = str(deal.get("risk_level", "medium")).lower()
    deal["risk_level"] = risk if risk in ["low","medium","high"] else "medium"

    verdict = str(deal.get("verdict", "BUY IF NEGOTIATED LOWER")).upper()
    deal["verdict"] = verdict if verdict in {"BUY","BUY IF NEGOTIATED LOWER","SKIP"} else "BUY IF NEGOTIATED LOWER"

    item.setdefault("name", "Unknown item")
    item.setdefault("brand", "")
    item.setdefault("model", "")
    item.setdefault("condition", "unknown")

    if not isinstance(msg, str) or not msg.strip():
        msg = "AI analysis completed."

    return {"item": item, "market_estimate": market, "deal_analysis": deal, "assistant_message": msg.strip()}

def _client() -> Optional[OpenAI]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key: return None
    return OpenAI(api_key=api_key, timeout=20.0, max_retries=1)

def fast_multi_photo_item(images: List[bytes], filenames: List[str], hint: str="") -> Dict[str, Any]:
    client = _client()
    if not client:
        return _safe_fallback("OPENAI_API_KEY is not set on the server (Railway Variables).")
    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()

    content = [{"type":"input_text","text":f"Hint from user: {hint}".strip()}]
    for b, fn in zip(images, filenames):
        content.append({"type":"input_image","image_url":_b64_data_url(b, fn)})

    system_instructions = """
You are a personal resale assistant for European flea markets and second-hand finds.
Act like a street-smart reseller, not a chatbot. Be conservative.

Return STRICT JSON ONLY with EXACT keys:
{
  "item":{"name":"...","brand":"...","model":"...","condition":"..."},
  "market_estimate":{"resale_price_range_usd":[low,high],"confidence":"low|medium|high"},
  "deal_analysis":{"verdict":"BUY|BUY IF NEGOTIATED LOWER|SKIP","risk_level":"low|medium|high"},
  "assistant_message":"short direct advice"
}

Rules:
- JSON only. No markdown. No extra keys.
    - Never invent model numbers. If not visible use "unknown".
- Use multiple photos to improve identification/condition.
- If uncertain widen range and lower confidence.
""".strip()

    try:
        resp = client.responses.create(
            model=model,
            instructions=system_instructions,
            input=[{"role":"user","content":content}],
            max_output_tokens=380,
        )
        data = _parse_strict_json(resp.output_text or "")
        if not data:
            return _safe_fallback("Could not parse AI JSON output. Add clearer photos (logo/label/serial).")
        return _normalize_payload(data)
    except Exception as e:
        return _safe_fallback(f"OpenAI error: {str(e)}")

def deep_research_item(images: List[bytes], filenames: List[str], hint: str="", platform: str="ebay", asking_price_usd: Optional[float]=None) -> Dict[str, Any]:
    client = _client()
    if not client:
        return _safe_fallback("OPENAI_API_KEY is not set on the server (Railway Variables).")
    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini").strip()

    content = [{"type":"input_text","text":f"Platform: {platform}\nHint: {hint}\nAsking price USD: {asking_price_usd}"}]
    for b, fn in zip(images, filenames):
        content.append({"type":"input_image","image_url":_b64_data_url(b, fn)})

    system_instructions = """
You are a Deep Research resale assistant. User provided multiple photos to reduce uncertainty.
Goal: tighten resale range if possible and explain what improved.

Return STRICT JSON ONLY with EXACT keys:
{
  "item":{"name":"...","brand":"...","model":"...","condition":"..."},
  "market_estimate":{"resale_price_range_usd":[low,high],"confidence":"low|medium|high"},
  "deal_analysis":{"verdict":"BUY|BUY IF NEGOTIATED LOWER|SKIP","risk_level":"low|medium|high"},
  "assistant_message":"short direct advice"
}

Rules:
- JSON only. No markdown. No extra keys.
- Never invent model numbers.
- If cannot tighten, keep range wide and say why.
- assistant_message MUST mention:
  (1) what extra photos clarified
  (2) what risks remain
  (3) best place to sell given selected platform
""".strip()

    try:
        resp = client.responses.create(
            model=model,
            instructions=system_instructions,
            input=[{"role":"user","content":content}],
            max_output_tokens=520,
        )
        data = _parse_strict_json(resp.output_text or "")
        if not data:
            return _safe_fallback("Deep Research failed. Add label/serial/defect close-ups.")
        return _normalize_payload(data)
    except Exception as e:
        return _safe_fallback(f"Deep Research OpenAI error: {str(e)}")
