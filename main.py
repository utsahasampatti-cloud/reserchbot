from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from services.ebay_scout import ebay_scout
from services.openai_vision import _b64_data_url, build_queries, vision_quick_sniff


UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Treasure Sniffer Backend", version="1.0.0")

# CORS: allow local dev + Lovable preview + your domains later
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "service": "treasure-sniffer-backend"}


@app.post("/api/debug-upload")
async def debug_upload(
    image: UploadFile = File(...),
    hint: Optional[str] = Form(None),
):
    return {
        "got_image": True,
        "filename": image.filename,
        "content_type": image.content_type,
        "hint": hint,
    }


def _safe_save(upload: UploadFile, content: bytes) -> Tuple[str, str]:
    ext = ".bin"
    if upload.content_type == "image/png":
        ext = ".png"
    elif upload.content_type == "image/jpeg":
        ext = ".jpg"
    elif upload.content_type == "image/webp":
        ext = ".webp"

    name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(content)
    return name, path


def _parse_model_json(text: str) -> Dict[str, Any]:
    # Hardening against stray text
    text = text.strip()
    # Try direct
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try find first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    return {
        "item": {"name": "unknown", "brand": "unknown", "model": "unknown", "condition": "unknown"},
        "market_estimate": {"resale_price_range_usd": [0, 0], "confidence": "low"},
        "risk_level": "medium",
        "notes": ["Could not parse model JSON output"],
    }


def _buffer_usd(risk_level: str, confidence: str) -> float:
    base = 15.0
    if risk_level == "low":
        base = 10.0
    if risk_level == "high":
        base = 30.0
    if confidence == "low":
        base += 10.0
    return float(base)


def _deal_math(
    resale_range: List[float],
    confidence: str,
    risk_level: str,
    asking_price_usd: Optional[float],
    fees_pct: float = 0.13,
    target_roi: float = 0.70,
) -> Dict[str, Any]:
    low, high = 0.0, 0.0
    try:
        low = float(resale_range[0])
        high = float(resale_range[1])
    except Exception:
        low, high = 0.0, 0.0

    conservative_resale = max(0.0, low)
    net_resale = conservative_resale * (1.0 - fees_pct)
    buffer = _buffer_usd(risk_level, confidence)

    verdict = "BUY IF NEGOTIATED LOWER"
    est_profit = None
    roi_pct = None

    negotiation: Dict[str, Any] = {
        "suggested_offer_usd": None,
        "max_buy_usd": None,
        "roi_if_offer_pct": None,
        "profit_if_offer_usd": None,
    }

    if asking_price_usd is None:
        # no price -> ask user
        return {
            "asking_price_usd": None,
            "fees_pct": int(round(fees_pct * 100)),
            "buffer_usd": round(buffer, 2),
            "estimated_profit_usd": None,
            "roi_pct": None,
            "verdict": verdict,
            "risk_level": risk_level,
            "negotiation": negotiation,
        }

    ap = float(asking_price_usd)
    est_profit = round(net_resale - ap - buffer, 2)
    if ap > 0:
        roi_pct = int(round((est_profit / ap) * 100))

    # Suggested offer for strong ROI
    if net_resale - buffer > 0:
        suggested = (net_resale - buffer) / (1.0 + target_roi)
        suggested = max(0.0, suggested)
        # round down to "nice" numbers
        if suggested >= 20:
            suggested = float(int(suggested // 5) * 5)
        else:
            suggested = float(int(suggested))
        negotiation["suggested_offer_usd"] = suggested

        max_buy = (net_resale - buffer) / (1.0 + 0.15)  # min ROI ~15%
        max_buy = max(0.0, max_buy)
        if max_buy >= 20:
            max_buy = float(int(max_buy // 5) * 5)
        else:
            max_buy = float(int(max_buy))
        negotiation["max_buy_usd"] = max_buy

        if suggested > 0:
            p2 = round(net_resale - suggested - buffer, 2)
            negotiation["profit_if_offer_usd"] = p2
            negotiation["roi_if_offer_pct"] = int(round((p2 / suggested) * 100)) if suggested else None

    # Verdict rules
    # Conservative + protects user
    if est_profit < 5 or (roi_pct is not None and roi_pct < 15) or risk_level == "high":
        verdict = "SKIP"
    elif (roi_pct is not None and roi_pct >= 40) and risk_level in {"low", "medium"}:
        verdict = "BUY"
    else:
        verdict = "BUY IF NEGOTIATED LOWER"

    return {
        "asking_price_usd": ap,
        "fees_pct": int(round(fees_pct * 100)),
        "buffer_usd": round(buffer, 2),
        "estimated_profit_usd": est_profit,
        "roi_pct": roi_pct,
        "verdict": verdict,
        "risk_level": risk_level,
        "negotiation": negotiation,
    }


def _ui_fields(data: Dict[str, Any]) -> List[Dict[str, str]]:
    item = data.get("item", {}) or {}
    market = data.get("market_estimate", {}) or {}
    deal = data.get("deal_analysis", {}) or {}

    rr = market.get("resale_price_range_usd") or [0, 0]
    rr_text = f"${rr[0]}–${rr[1]}" if isinstance(rr, list) and len(rr) == 2 else "unknown"

    fields = [
        {"label": "Name", "value": str(item.get("name", "unknown"))},
        {"label": "Brand", "value": str(item.get("brand", "unknown"))},
        {"label": "Model", "value": str(item.get("model", "unknown"))},
        {"label": "Condition (visible)", "value": str(item.get("condition", "unknown"))},
        {"label": "Resale range (USD)", "value": rr_text},
        {"label": "Est. confidence", "value": str(market.get("confidence", "low"))},
        {"label": "Risk", "value": str(data.get("risk_level", "medium"))},
        {"label": "Verdict", "value": str(deal.get("verdict", "BUY IF NEGOTIATED LOWER"))},
    ]

    ap = deal.get("asking_price_usd")
    if ap is not None:
        fields.append({"label": "Asking price (USD)", "value": f"${ap}"})
    if deal.get("estimated_profit_usd") is not None:
        fields.append({"label": "Est. profit (USD)", "value": f"${deal['estimated_profit_usd']}"})
    if deal.get("roi_pct") is not None:
        fields.append({"label": "ROI (est.)", "value": f"{deal['roi_pct']}%"})

    nego = deal.get("negotiation") or {}
    if nego.get("suggested_offer_usd") is not None:
        fields.append({"label": "Suggested offer", "value": f"${nego['suggested_offer_usd']}"})
    if nego.get("max_buy_usd") is not None:
        fields.append({"label": "Max buy price", "value": f"${nego['max_buy_usd']}"})
    if nego.get("roi_if_offer_pct") is not None:
        fields.append({"label": "ROI if offer", "value": f"{nego['roi_if_offer_pct']}%"})

    return fields


def _summary(data: Dict[str, Any], mode: str) -> str:
    item = data.get("item", {}) or {}
    market = data.get("market_estimate", {}) or {}
    deal = data.get("deal_analysis", {}) or {}

    rr = market.get("resale_price_range_usd") or [0, 0]
    rr_text = f"${rr[0]}–${rr[1]} USD" if isinstance(rr, list) and len(rr) == 2 else "unknown"

    lines = []
    lines.append(f"{item.get('brand','unknown')} {item.get('model','unknown')} — {item.get('name','item')}")
    lines.append(f"Condition (visible): {item.get('condition','unknown')}")
    lines.append(f"Estimated resale range: {rr_text} (confidence: {market.get('confidence','low')})")
    lines.append(f"Verdict: {deal.get('verdict','BUY IF NEGOTIATED LOWER')} | Risk: {data.get('risk_level','medium')}")

    if deal.get("asking_price_usd") is None:
        lines.append("How much can you buy this item for right now? (Enter asking price and sniff again.)")
    else:
        lines.append(f"At ${deal['asking_price_usd']}, est. profit: ${deal.get('estimated_profit_usd')} | ROI: {deal.get('roi_pct')}%")
        nego = deal.get("negotiation") or {}
        if nego.get("suggested_offer_usd") is not None:
            lines.append(f"Negotiation: offer ${nego['suggested_offer_usd']} (ROI ~{nego.get('roi_if_offer_pct')}%). Max buy: ${nego.get('max_buy_usd')}.")
        if mode == "deep":
            lines.append("Deep Research: eBay sold comps were used to anchor the market estimate.")
        else:
            lines.append("Quick Sniff: rough estimate. Use Deep Research for real eBay comps.")

    return "\n".join([l for l in lines if l])


@app.post("/api/describe")
async def describe(
    # Support both single and multi upload
    image: Optional[UploadFile] = File(None),
    images: Optional[List[UploadFile]] = File(None),
    hint: Optional[str] = Form(None),
    asking_price_usd: Optional[float] = Form(None),
    mode: str = Form("quick"),  # quick | deep
):
    # Collect uploads
    uploads: List[UploadFile] = []
    if images:
        uploads.extend(images)
    if image:
        uploads.append(image)
    if not uploads:
        return {"ok": False, "error": "No image uploaded"}

    saved_files = []
    openai_images = []
    for up in uploads[:5]:
        b = await up.read()
        fname, path = _safe_save(up, b)
        saved_files.append({"saved": True, "filename": fname, "path": path})
        ct = up.content_type or "image/png"
        openai_images.append({"filename": up.filename or fname, "data_url": _b64_data_url(b, ct)})

    # Vision quick sniff (OpenAI)
    raw = vision_quick_sniff(openai_images)
    model_json = _parse_model_json(raw.get("raw_json_text", ""))

    item = model_json.get("item") or {}
    market = model_json.get("market_estimate") or {"resale_price_range_usd": [0, 0], "confidence": "low"}
    risk_level = model_json.get("risk_level") or "medium"
    notes = model_json.get("notes") or []

    # Deep Research: eBay scout anchors market estimate
    ebay = None
    if mode.strip().lower() == "deep":
        queries = build_queries(item=item, hint=hint or "")
        ebay = await ebay_scout(queries=queries, limit_each=6)
        rng = ebay.get("overall_sold_price_range_usd")
        if isinstance(rng, list) and len(rng) == 2 and (rng[0] or rng[1]):
            market["resale_price_range_usd"] = [int(rng[0]), int(rng[1])]
            market["confidence"] = "medium" if market.get("confidence") != "high" else "high"
        else:
            notes.append("eBay scout: no close sold comps found; estimate may be unreliable.")
            market["confidence"] = "low"

    deal = _deal_math(
        resale_range=market.get("resale_price_range_usd") or [0, 0],
        confidence=market.get("confidence", "low"),
        risk_level=risk_level,
        asking_price_usd=asking_price_usd,
    )

    data = {
        "item": {
            "name": item.get("name", "unknown"),
            "brand": item.get("brand", "unknown"),
            "model": item.get("model", "unknown"),
            "condition": item.get("condition", "unknown"),
        },
        "market_estimate": {
            "resale_price_range_usd": market.get("resale_price_range_usd", [0, 0]),
            "confidence": market.get("confidence", "low"),
        },
        "risk_level": risk_level,
        "deal_analysis": deal,
        "notes": notes,
        "mode": mode,
        "ebay": ebay,
    }

    ui = {
        "fields": _ui_fields(data),
        "summary": _summary(data, mode=mode.strip().lower()),
    }

    return {
        "data": data,
        "ui": ui,
        "files": saved_files,
    }
