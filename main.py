import json
import os
from typing import List, Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from services.openai_vision import _b64_data_url, vision_quick_sniff

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для MVP ок
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICE_NAME = "treasure-sniffer-backend"


def is_safe_mode() -> bool:
    if os.getenv("SAFE_MODE", "").strip() == "1":
        return True
    if not os.getenv("OPENAI_API_KEY"):
        return True
    return False


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "safe_mode": is_safe_mode(),
    }


@app.post("/api/debug-upload")
async def debug_upload(
    image: UploadFile = File(...),
    hint: Optional[str] = Form(None),
):
    data = await image.read()
    return {
        "got_image": True,
        "filename": image.filename,
        "content_type": image.content_type,
        "bytes": len(data),
        "hint": hint,
    }


def safe_mode_response():
    return {
        "ui": {
            "fields": {
                "Item": "Temporary fallback",
                "Condition": "unknown",
                "Resale Price Range": "$0 - $0",
                "Confidence": "low",
                "Risk Level": "low",
                "Verdict": "SKIP",
            },
            "summary": "Service is running in safe mode.",
        }
    }


@app.post("/api/describe")
async def describe(
    images: List[UploadFile] = File(...),
    hint: Optional[str] = Form(None),
    asking_price: Optional[float] = Form(None),
    deep: Optional[bool] = Form(False),
    device_id: Optional[str] = Form(None),
):
    # 1) Safe mode
    if is_safe_mode():
        return safe_mode_response()

    # 2) Read images
    img_payload = []
    for img in images[:6]:  # ліміт щоб не спалити токени
        b = await img.read()
        mime = img.content_type or "image/jpeg"
        img_payload.append({"type": "input_image", "image_url": _b64_data_url(b, mime=mime)})

    # 3) Prompt (простий, але стабільний)
    price_line = f"Asking price: {asking_price} USD." if asking_price is not None else "Asking price: unknown."
    hint_line = f"User hint: {hint}" if hint else "User hint: none."
    mode_line = "Mode: deep research (use wider range but be more careful)." if deep else "Mode: quick sniff."

    prompt = f"""
You are a street-smart resale assistant for European flea markets.

Return ONLY valid JSON in this exact structure:

{{
  "ui": {{
    "fields": {{
      "Item": "...",
      "Condition": "...",
      "Resale Price Range": "...",
      "Confidence": "low|medium|high",
      "Risk Level": "low|medium|high",
      "Verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP"
    }},
    "summary": "Short direct advice."
  }}
}}

Rules:
- Be conservative.
- If asking_price is provided, verdict must react to it (margin logic).
- If you can't identify item, set confidence low and widen range.

Context:
{hint_line}
{price_line}
{mode_line}
""".strip()

    # 4) Call OpenAI
    try:
        raw = vision_quick_sniff(img_payload, prompt=prompt, model="gpt-4o-mini", max_output_tokens=700)
    except Exception as e:
        # повертаємо контрольований фолбек, але з діагностикою в summary
        r = safe_mode_response()
        r["ui"]["summary"] = f"OpenAI call failed. {type(e).__name__}: {str(e)[:160]}"
        return r

    # 5) Parse JSON safely
    try:
        data = json.loads(raw)
        # мінімальна валідація
        if not isinstance(data, dict) or "ui" not in data:
            raise ValueError("Bad JSON schema")
        return data
    except Exception:
        # якщо модель дала не-json — теж повертаємо фолбек
        r = safe_mode_response()
        r["ui"]["summary"] = "Model returned invalid JSON. Try again with clearer photos."
        return r
