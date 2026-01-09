# main.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from services.openai_vision import call_vision_pricing

app = FastAPI(title="Treasure Sniffer Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _safe_float(v: Optional[str]) -> Optional[float]:
    try:
        if v is None:
            return None
        s = str(v).strip().replace(",", ".")
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def _verdict_badge(verdict: str) -> str:
    v = (verdict or "").strip().upper()
    if v in {"BUY", "BUY IF NEGOTIATED LOWER", "SKIP"}:
        return v
    return "SKIP"


def _confidence(x: str) -> str:
    x = (x or "").strip().lower()
    return x if x in {"low", "medium", "high"} else "low"


def _risk(x: str) -> str:
    x = (x or "").strip().lower()
    return x if x in {"low", "medium", "high"} else "medium"


def _ui_from_model(model_json: Dict[str, Any], asking_price: Optional[float]) -> Dict[str, Any]:
    item = model_json.get("item", {}) or {}
    market = model_json.get("market_estimate", {}) or {}
    deal = model_json.get("deal_analysis", {}) or {}

    price_range = market.get("resale_price_range_usd", [0, 0])
    low = price_range[0] if isinstance(price_range, list) and len(price_range) == 2 else 0
    high = price_range[1] if isinstance(price_range, list) and len(price_range) == 2 else 0

    verdict = _verdict_badge(deal.get("verdict", "SKIP"))
    conf = _confidence(market.get("confidence", "low"))
    risk = _risk(deal.get("risk_level", "medium"))

    # Basic ROI/margin helpers (optional)
    margin_note = ""
    if asking_price is not None and high:
        # conservative: use low end vs asking
        try:
            margin = float(low) - float(asking_price)
            margin_note = f"Conservative margin vs asking: ${margin:.2f}"
        except Exception:
            margin_note = ""

    fields = [
        {"label": "Verdict", "value": verdict, "type": "verdict"},
        {"label": "Item", "value": item.get("name", "unknown")},
        {"label": "Brand", "value": item.get("brand", "unknown")},
        {"label": "Model", "value": item.get("model", "unknown")},
        {"label": "Condition", "value": item.get("condition", "unknown")},
        {"label": "Resale Price Range", "value": f"${low} — ${high}"},
        {"label": "Confidence", "value": conf},
        {"label": "Risk Level", "value": risk},
    ]

    if asking_price is not None:
        fields.insert(6, {"label": "Asking Price", "value": f"${asking_price}"})
        if margin_note:
            fields.append({"label": "Note", "value": margin_note})

    summary = model_json.get("assistant_message", "") or "No summary."
    return {"fields": fields, "summary": summary}


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


@app.post("/api/describe")
async def describe(
    # NEW: multi-image
    images: Optional[List[UploadFile]] = File(None),
    # backward-compatible single image
    image: Optional[UploadFile] = File(None),
    hint: Optional[str] = Form(None),
    asking_price: Optional[str] = Form(None),
    language: Optional[str] = Form("en"),
    mode: Optional[str] = Form("quick"),  # quick|deep
):
    asking = _safe_float(asking_price)

    # Collect images
    uploads: List[UploadFile] = []
    if images:
        uploads.extend(images)
    if image:
        uploads.append(image)

    if not uploads:
        # Lovable error earlier was "images required" — we respond cleanly:
        return {
            "ok": False,
            "error": "NO_IMAGES",
            "message": "Please upload at least one photo.",
            "ui": {
                "fields": [],
                "summary": "No photos received. Upload at least one image and try again.",
            },
        }

    # Read bytes
    openai_images: List[Tuple[bytes, str]] = []
    for u in uploads[:8]:
        b = await u.read()
        mime = u.content_type or "image/jpeg"
        openai_images.append((b, mime))

    # Call OpenAI safely
    try:
        model_json = call_vision_pricing(
            images=openai_images,
            hint=hint,
            asking_price=asking,
            currency="USD",
            language=(language or "en"),
            mode=(mode or "quick"),
        )

        ui = _ui_from_model(model_json, asking)

        return {
            "ok": True,
            "data": model_json,
            "ui": ui,
        }

    except Exception as e:
        # Never crash UI — return a friendly error + raw details
        raw = str(e)
        return {
            "ok": False,
            "error": "AI_CALL_FAILED",
            "message": "AI call failed. Try again in a moment.",
            "raw_error": raw,
            "ui": {
                "fields": [],
                "summary": "Couldn’t sniff the treasure this time. Try again.",
            },
        }
