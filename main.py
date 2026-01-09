# app/main.py
import os
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS (щоб Lovable не падав)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/describe")
async def describe(images: list[UploadFile] = File(...)):
    # СТАБІЛЬНА заглушка
    return {
        "ui": {
            "fields": {
                "Item": "Temporary fallback",
                "Condition": "unknown",
                "Resale Price Range": "$0 – $0",
                "Confidence": "low",
                "Risk Level": "low",
                "Verdict": "SKIP"
            },
            "summary": "Service is running in safe mode."
        }
    }
