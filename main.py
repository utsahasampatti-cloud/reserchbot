import os
import json
import time
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.openai_vision import analyze_with_openai

app = FastAPI(title="Treasure Sniffer Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для Lovable ок; потім звузимо
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Simple in-memory daily limit (demo). Replace later with Redis/DB + device key.
# Resets on server restart; good enough to stop crashes + show message.
_DAILY_LIMIT_FREE = int(os.getenv("DAILY_LIMIT_FREE", "5"))
_usage: Dict[str, Dict[str, Any]] = {}  # {device_id: {"day": "YYYY-MM-DD", "count": int}}

def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())

def _device_key(device_id: Optional[str]) -> str:
    return (device_id or "anonymous").strip()[:200]

def _check_and_inc_limit(device_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Returns an error dict if limit reached, otherwise increments count and returns None.
    """
    plan = os.getenv("PLAN", "free")
    if plan != "free":
        return None

    dev = _device_key(device_id)
    day = _today_key()
    rec = _usage.get(dev) or {"day": day, "count": 0}
    if rec["day"] != day:
        rec = {"day": day, "count": 0}

    if rec["count"] >= _DAILY_LIMIT_FREE:
        return {
            "error": "LIMIT_REACHED",
            "plan": "free",
            "limit_per_day": _DAILY_LIMIT_FREE,
            "message": "Daily limit reached. Come back tomorrow.",
        }

    rec["count"] += 1
    _usage[dev] = rec
    return None


@app.get("/health")
def health():
    # Helpful: shows if OpenAI key is set
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {"ok": True, "service": "treasure-sniffer-backend", "openai_configured": has_key}


@app.get("/")
def root():
    # optional root so Railway doesn't show 404
    return {"ok": True, "hint": "Use /health or POST /api/describe"}


@app.post("/api/debug-upload")
async def debug_upload(
    image: Optional[UploadFile] = File(None),
    images: Optional[List[UploadFile]] = File(None),
    hint: Optional[str] = Form(None),
):
    got = []
    if image is not None:
        got.append({"filename": image.filename, "content_type": image.content_type})
    if images:
        got.extend([{"filename": f.filename, "content_type": f.content_type} for f in images])
    return {"got_files": got, "hint": hint}


@app.post("/api/describe")
async def describe(
    # Backward/forward compatibility:
    # - Lovable може слати `images` (multiple)
    # - старий фронт може слати `image` (single)
    image: Optional[UploadFile] = File(None),
    images: Optional[List[UploadFile]] = File(None),
    hint: Optional[str] = Form(None),
    asking_price: Optional[float] = Form(None),
    device_id: Optional[str] = Form(None),
    deep: Optional[bool] = Form(False),
    platform: Optional[str] = Form(None),  # "ebay" later
):
    # 1) normalize files
    files: List[UploadFile] = []
    if images:
        files.extend([f for f in images if f is not None])
    if image is not None:
        files.append(image)

    if not files:
        # This matches your error screenshot: Field required
        return JSONResponse(
            status_code=422,
            content={"detail": [{"type": "missing", "loc": ["body", "images"], "msg": "Field required", "input": None}]},
        )

    # 2) limit check (free)
    limit_err = _check_and_inc_limit(device_id)
    if limit_err:
        # UI-friendly error
        return JSONResponse(
            status_code=200,
            content={
                "ui": {
                    "fields": {
                        "Item": "—",
                        "Condition": "—",
                        "Resale Price Range": "$0 — $0",
                        "Confidence": "low",
                        "Risk Level": "low",
                        "Verdict": "SKIP",
                    },
                    "summary": "На сьогодні ліміт вичерпано. Приходь завтра 🦴",
                },
                "error": limit_err,
            },
        )

    # 3) read bytes
    openai_images = []
    debug_files = []
    for f in files[:6]:  # safety cap
        data = await f.read()
        openai_images.append({"filename": f.filename, "content_type": f.content_type, "bytes": data})
        debug_files.append({"filename": f.filename, "content_type": f.content_type, "size": len(data)})

    # 4) call OpenAI (vision + pricing logic)
    try:
        result = analyze_with_openai(
            images=openai_images,
            hint=hint,
            asking_price=asking_price,
            deep=bool(deep),
            platform=platform,
        )
        # Ensure UI shape always exists
        ui = result.get("ui") or {}
        fields = ui.get("fields") or {}
        summary = ui.get("summary") or "No summary."

        return {
            "ui": {"fields": fields, "summary": summary},
            "debug": {"files": debug_files},
        }

    except Exception as e:
        # Never crash UI
        return JSONResponse(
            status_code=200,
            content={
                "ui": {
                    "fields": {
                        "Item": "Temporary fallback",
                        "Condition": "unknown",
                        "Resale Price Range": "$0 — $0",
                        "Confidence": "low",
                        "Risk Level": "low",
                        "Verdict": "SKIP",
                    },
                    "summary": "Service is running in safe mode.",
                },
                "error": {"error": "SAFE_MODE", "message": str(e)},
                "debug": {"files": debug_files},
            },
        )
