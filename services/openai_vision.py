import os
import json
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.openai_vision import vision_quick_sniff

app = FastAPI()

# --- CORS (для Lovable / mobile / web) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Utils ---
def _safe(val, fallback="unknown"):
    if val is None:
        return fallback
    if isinstance(val, str) and val.strip() == "":
        return fallback
    return val

def _to_float_or_none(x):
    try:
        if x is None:
            return None
        s = str(x).strip().replace(",", ".")
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


# --- Health ---
@app.get("/health")
def health():
    return {"ok": True, "service": "treasure-sniffer-backend"}


# --- Main API ---
@app.post("/api/describe")
async def describe(
    images: List[UploadFile] = File(...),
    hint: Optional[str] = Form(None),
    asking_price: Optional[str] = Form(None),
    language: Optional[str] = Form("en"),
):
    if not images or len(images) == 0:
        return JSONResponse(
            status_code=422,
            content={"error": "NO_IMAGES", "message": "At least one image is required"},
        )

    # read image bytes
    image_blobs = []
    for img in images:
        try:
            data = await img.read()
            image_blobs.append({
                "filename": img.filename,
                "content_type": img.content_type,
                "bytes": data,
            })
        except Exception:
            pass

    if not image_blobs:
        return JSONResponse(
            status_code=422,
            content={"error": "IMAGE_READ_FAILED"},
        )

    asking_price_val = _to_float_or_none(asking_price)

    # --- Vision + reasoning ---
    try:
        ai_result = vision_quick_sniff(
            images=image_blobs,
            hint=hint,
            asking_price=asking_price_val,
            language=language,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": "VISION_FAILED",
                "message": str(e),
            },
        )

    # --- Normalize response for UI ---
    item = ai_result.get("item", {})
    market = ai_result.get("market_estimate", {})
    deal = ai_result.get("deal_analysis", {})

    ui_fields = [
        {
            "label": "Item",
            "value": _safe(item.get("name")),
        },
        {
            "label": "Condition",
            "value": _safe(item.get("condition")),
        },
        {
            "label": "Resale Price Range",
            "value": (
                f"${market['resale_price_range_usd'][0]} – ${market['resale_price_range_usd'][1]}"
                if isinstance(market.get("resale_price_range_usd"), list)
                else "unknown"
            ),
        },
        {
            "label": "Confidence",
            "value": _safe(market.get("confidence")),
        },
        {
            "label": "Risk Level",
            "value": _safe(deal.get("risk_level")),
        },
    ]

    verdict = deal.get("verdict", "SKIP")

    ui_fields.insert(
        0,
        {
            "label": "VERDICT",
            "value": verdict,
            "type": "verdict",
        },
    )

    return {
        "ui": {
            "fields": ui_fields,
            "summary": _safe(ai_result.get("assistant_message"), ""),
        },
        "raw": ai_result,
    }


# --- Debug upload (optional but useful) ---
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


# --- Local run ---
if name == "__main__":
    import uvicorn port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
