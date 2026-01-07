import os
import uuid
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Form, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import db
from services.limits import check_limit, register_usage
from services.openai_vision import fast_multi_photo_item, deep_research_item
from services.emailer import send_email
from services.stripe_webhook import handle_stripe_webhook

# ----------------------------
# App init
# ----------------------------
app = FastAPI(title="Flea Assistant Backend", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

db.init_db()

# ----------------------------
# Helpers
# ----------------------------
def get_device_id(x_device_id: Optional[str]) -> str:
    if x_device_id:
        return x_device_id.strip()
    return str(uuid.uuid4())

async def save_files(files: List[UploadFile]) -> tuple[list[bytes], list[str]]:
    images: list[bytes] = []
    filenames: list[str] = []

    for f in files:
        content = await f.read()
        images.append(content)
        filenames.append(f.filename or "image.png")

        # save for debug / audit
        safe = f"{uuid.uuid4().hex}_{f.filename}"
        path = os.path.join(UPLOAD_DIR, safe)
        with open(path, "wb") as out:
            out.write(content)

    return images, filenames

# ----------------------------
# Health
# ----------------------------
@app.get("/health")
def health():
    return {"ok": True, "service": "flea-backend"}

# ----------------------------
# Debug upload (for frontend testing)
# ----------------------------
@app.post("/api/debug-upload")
async def debug_upload(
    image: UploadFile = File(...),
    hint: Optional[str] = Form(None),
):
    content = await image.read()
    return {
        "got_image": True,
        "filename": image.filename,
        "content_type": image.content_type,
        "hint": hint,
        "size_bytes": len(content),
    }

# ----------------------------
# FAST ANALYSIS (multi-photo)
# ----------------------------
@app.post("/api/describe")
async def describe(
    request: Request,
    images: List[UploadFile] = File(...),
    hint: Optional[str] = Form(""),
    asking_price_usd: Optional[float] = Form(None),
    x_device_id: Optional[str] = Header(None),
):
    device_id = get_device_id(x_device_id)

    limit = check_limit(device_id)
    if not limit.allowed:
        return JSONResponse(
            status_code=402,
            content={
                "error": "LIMIT_REACHED",
                "plan": limit.plan,
                "remaining": limit.remaining,
                "limit": limit.limit,
            },
        )

    img_bytes, filenames = await save_files(images)

    result = fast_multi_photo_item(
        images=img_bytes,
        filenames=filenames,
        hint=hint or "",
    )

    register_usage(device_id)

    return {
        "data": result,
        "meta": {
            "device_id": device_id,
            "plan": limit.plan,
            "remaining": limit.remaining - 1,
        },
    }

# ----------------------------
# DEEP RESEARCH (paid / optional)
# ----------------------------
@app.post("/api/deep-research")
async def deep_research(
    request: Request,
    images: List[UploadFile] = File(...),
    hint: Optional[str] = Form(""),
    platform: Optional[str] = Form("ebay"),
    asking_price_usd: Optional[float] = Form(None),
    x_device_id: Optional[str] = Header(None),
):
    device_id = get_device_id(x_device_id)

    limit = check_limit(device_id)
    if not limit.allowed:
        return JSONResponse(
            status_code=402,
            content={
                "error": "LIMIT_REACHED",
                "plan": limit.plan,
                "remaining": limit.remaining,
                "limit": limit.limit,
            },
        )

    img_bytes, filenames = await save_files(images)

    result = deep_research_item(
        images=img_bytes,
        filenames=filenames,
        hint=hint or "",
        platform=platform or "ebay",
        asking_price_usd=asking_price_usd,
    )

    register_usage(device_id)

    return {
        "data": result,
        "meta": {
            "device_id": device_id,
            "plan": limit.plan,
            "remaining": limit.remaining - 1,
            "platform": platform,
        },
    }

# ----------------------------
# EMAIL GATE (free → email unlock)
# ----------------------------
@app.post("/api/register-email")
async def register_email(
    email: str = Form(...),
    x_device_id: Optional[str] = Header(None),
):
    device_id = get_device_id(x_device_id)
    email = email.strip().lower()

    if "@" not in email:
        raise HTTPException(status_code=400, detail="INVALID_EMAIL")

    db.set_email_for_device(device_id, email)

    subject = "Flea Assistant — unlocked"
    text = f"""Thanks!

You now have:
• 10 evaluations per day (free)
• Multi-photo analysis
• Smarter resale estimates

Happy hunting 🐾
"""

    send_email(email, subject, text)

    return {"ok": True, "device_id": device_id, "plan": "email"}

# ----------------------------
# LICENSE ACTIVATE (paid)
# ----------------------------
@app.post("/api/activate-license")
async def activate_license(
    license_key: str = Form(...),
    x_device_id: Optional[str] = Header(None),
):
    device_id = get_device_id(x_device_id)
    ok, status = db.bind_license_to_device(license_key, device_id)

    if not ok:
        raise HTTPException(status_code=400, detail=status)

    return {"ok": True, "status": status, "plan": "paid"}

# ----------------------------
# STRIPE WEBHOOK
# ----------------------------
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    status, resp = handle_stripe_webhook(payload, sig)
    return JSONResponse(status_code=status, content=resp)
