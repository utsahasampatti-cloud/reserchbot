import json
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.openai_vision import vision_quick_sniff

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "service": "treasure-sniffer-backend"}


@app.post("/api/debug-upload")
async def debug_upload(image: UploadFile = File(...), hint: Optional[str] = Form(None)):
    # simple sanity check endpoint
    return {
        "got_image": True,
        "filename": image.filename,
        "content_type": image.content_type,
        "hint": hint,
    }


@app.post("/api/describe")
async def describe(
    images: List[UploadFile] = File(...),
    hint: Optional[str] = Form(None),
    asking_price: Optional[float] = Form(None),
    currency: str = Form("USD"),
    language: str = Form("en"),
):
    try:
        openai_images = []
        for f in images:
            b = await f.read()
            openai_images.append({"data": b, "content_type": f.content_type or "image/jpeg"})

        raw = vision_quick_sniff(
            images=openai_images,
            hint=hint,
            asking_price=asking_price,
            currency=currency,
            language=language,
        )

        # Try parse JSON; if model returns text, fallback safely
        try:
            data = json.loads(raw)
        except Exception:
            data = {
                "ui": {
                    "fields": {
                        "Item": "Could not parse model output",
                        "Condition": "unknown",
                        "Resale Price Range": "$0 - $0",
                        "Confidence": "low",
                        "Risk Level": "low",
                        "Verdict": "SKIP",
                    },
                    "summary": raw[:500],
                }
            }

        return JSONResponse(content=data)

    except Exception as e:
        # safe-mode response so frontend never crashes
        return JSONResponse(
            status_code=200,
            content={
                "ui": {
                    "fields": {
                        "Item": "Temporary fallback",
                        "Condition": "unknown",
                        "Resale Price Range": "$0 - $0",
                        "Confidence": "low",
                        "Risk Level": "low",
                        "Verdict": "SKIP",
                    },
                    "summary": f"OpenAI call failed.\n{type(e).__name__}: {str(e)}",
                }
            },
        )
