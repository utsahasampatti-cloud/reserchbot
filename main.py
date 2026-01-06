#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware

from services.openai_vision import describe_item

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Deal math
EBAY_FEE_RATE = 0.13
FIXED_COST_USD = 20.0
MIN_PROFIT_USD = 60.0
MIN_ROI = 0.30

app = FastAPI(title="Flea Assistant Backend (Resale Agent + UI Safe)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # DEV only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def calc_deal(asking_price: float, resale_range: List[float]) -> Dict[str, Any]:
    low, high = float(resale_range[0]), float(resale_range[1])
    mid = (low + high) / 2.0
    net_resale = mid * (1.0 - EBAY_FEE_RATE) - FIXED_COST_USD
    profit = net_resale - asking_price
    roi = (profit / asking_price) if asking_price > 0 else 0.0

    if profit >= MIN_PROFIT_USD and roi >= MIN_ROI:
        verdict = "BUY"
    elif profit > 0:
        verdict = "BUY IF NEGOTIATED LOWER"
    else:
        verdict = "SKIP"

    return {
        "resale_low_usd": round(low, 2),
        "resale_high_usd": round(high, 2),
        "resale_mid_usd": round(mid, 2),
        "net_resale_usd": round(net_resale, 2),
        "estimated_profit_usd": round(profit, 2),
        "roi": round(roi, 2),
        "verdict": verdict,
    }

def as_fields(item: Dict[str, Any], market: Dict[str, Any], deal: Dict[str, Any], computed: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    # Always return an array of {label, value} with NO undefined entries
    def add(label: str, value: Any):
        v = "" if value is None else str(value).strip()
        return {"label": label, "value": v}

    fields = []
    fields.append(add("Name", item.get("name")))
    fields.append(add("Brand", item.get("brand")))
    fields.append(add("Model", item.get("model")))
    fields.append(add("Condition (visible)", item.get("condition")))

    pr = market.get("resale_price_range_usd")
    if isinstance(pr, list) and len(pr) == 2:
        fields.append(add("Resale range (USD)", f"${pr[0]}–${pr[1]}"))
    else:
        fields.append(add("Resale range (USD)", ""))

    fields.append(add("Est. confidence", market.get("confidence")))
    fields.append(add("Risk", deal.get("risk_level")))
    fields.append(add("Verdict", deal.get("verdict")))

    if computed:
        fields.append(add("Resale mid (USD)", computed.get("resale_mid_usd")))
        fields.append(add("Net resale after fees (USD)", computed.get("net_resale_usd")))
        fields.append(add("Profit (USD)", computed.get("estimated_profit_usd")))
        fields.append(add("ROI", computed.get("roi")))

    # Ensure no item is None/undefined
    return [f for f in fields if isinstance(f, dict) and "label" in f and "value" in f]

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/debug-upload")
async def debug_upload(
    image: UploadFile = File(None),
    hint: str = Form(""),
):
    return {
        "got_image": bool(image),
        "filename": getattr(image, "filename", None),
        "content_type": getattr(image, "content_type", None),
        "hint": hint,
    }

@app.post("/api/describe")
async def describe(
    image: UploadFile = File(...),
    hint: str = Form(""),
    asking_price_usd: Optional[float] = Form(None),
):
    img_bytes = await image.read()

    ext = os.path.splitext(image.filename or "")[1]
    safe_name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, safe_name), "wb") as f:
        f.write(img_bytes)

    hint2 = hint.strip()
    if asking_price_usd is not None:
        hint2 = (hint2 + f"\nPurchase price offered now: {asking_price_usd} USD").strip()

    result = describe_item(img_bytes=img_bytes, filename=image.filename, hint=hint2)

    item = result.get("item") or {}
    market = result.get("market_estimate") or {}
    deal = result.get("deal_analysis") or {}
    msg = (result.get("assistant_message") or "").strip()

    price_range = market.get("resale_price_range_usd")
    computed = None
    if asking_price_usd is not None and isinstance(price_range, list) and len(price_range) == 2:
        computed = calc_deal(float(asking_price_usd), price_range)
        deal["asking_price_usd"] = float(asking_price_usd)
        deal["estimated_profit_usd"] = computed["estimated_profit_usd"]
        deal["roi"] = computed["roi"]
        deal["net_resale_usd"] = computed["net_resale_usd"]
        deal["verdict"] = computed["verdict"]

    summary_lines = []
    headline = " ".join([x for x in [item.get("brand"), item.get("model"), item.get("name")] if x]).strip() or "Item"
    summary_lines.append(headline)

    if item.get("condition"):
        summary_lines.append(f"Condition (visible): {item['condition']}")

    if isinstance(price_range, list) and len(price_range) == 2:
        summary_lines.append(f"Estimated resale range: ${price_range[0]}–${price_range[1]} USD (confidence: {market.get('confidence','?')})")

    if asking_price_usd is None:
        summary_lines.append("How much can you buy this item for right now?")
    else:
        if computed:
            summary_lines.append(f"Verdict: {computed['verdict']}")
            summary_lines.append(f"Profit: ${computed['estimated_profit_usd']} USD | ROI: {computed['roi']} | Net resale: ${computed['net_resale_usd']} USD")

    if deal.get("risk_level"):
        summary_lines.append(f"Risk: {deal['risk_level']}")
    if msg:
        summary_lines.append(msg)

    return {
        "ui": {
            "fields": as_fields(item, market, deal, computed),   # <- Lovable should render this list
            "summary": "\n".join([s for s in summary_lines if s]),
        },
        "data": {
            "item": item,
            "market_estimate": market,
            "deal_analysis": deal,
            "computed": computed,
        },
        "file": {
            "saved": True,
            "filename": safe_name,
            "path": f"uploads/{safe_name}",
        },
    }
    @app.get("/")
def root():
    return {"ok": True, "service": "flea-backend"}

@app.get("/health")
def health():
    return {"ok": True}

