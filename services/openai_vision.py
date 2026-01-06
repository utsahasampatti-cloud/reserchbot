#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import base64
import mimetypes
import re
from typing import Dict, Any, Optional

import requests

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def _data_url_from_bytes(img_bytes: bytes, filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    if not mime:
        mime = "application/octet-stream"
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _extract_json_candidate(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"```json\s*(\{.*?\})\s*```", t, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"```\s*(\{.*?\})\s*```", t, flags=re.DOTALL)
    if m2:
        return m2.group(1).strip()
    return t


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    candidate = _extract_json_candidate(text)
    try:
        return json.loads(candidate)
    except Exception:
        pass
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(candidate[first:last + 1])
        except Exception:
            pass
    return None


def describe_item(img_bytes: bytes, filename: str, hint: str = "") -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    image_data_url = _data_url_from_bytes(img_bytes, filename)

    # 🔥 SYSTEM PROMPT — RESALE AGENT
    system = (
        "You are a personal resale assistant for flea markets and second-hand finds.\n"
        "You think like an experienced reseller who must decide quickly whether an item is worth buying.\n\n"

        "You must analyze the item in FOUR phases and return ONLY valid JSON.\n\n"

        "PHASE 1 — IDENTIFICATION\n"
        "Identify the item from the image:\n"
        "- name\n"
        "- type\n"
        "- brand\n"
        "- model (if visible)\n"
        "- condition (ONLY what is visible)\n"
        "- confidence level\n\n"

        "PHASE 2 — MARKET ESTIMATION\n"
        "Estimate a realistic resale price RANGE in USD based on second-hand market knowledge.\n"
        "Do NOT give exact prices. Use a range and estimation confidence.\n\n"

        "PHASE 3 — USER PRICE CHECK\n"
        "If purchase price is NOT provided, include a question asking:\n"
        "\"How much can you buy this item for right now?\"\n\n"

        "PHASE 4 — DECISION\n"
        "If a purchase price IS provided:\n"
        "- calculate potential profit\n"
        "- consider resale fees and effort\n"
        "- give a verdict: BUY / BUY IF NEGOTIATED LOWER / SKIP\n\n"

        "STRICT RULES:\n"
        "- Return ONLY valid JSON\n"
        "- No markdown\n"
        "- No explanations outside JSON\n"
    )

    user = (
        "Return JSON with this structure:\n"
        "{\n"
        '  "item": {\n'
        '    "name": "",\n'
        '    "brand": "",\n'
        '    "model": "",\n'
        '    "condition": ""\n'
        "  },\n"
        '  "market_estimate": {\n'
        '    "resale_price_range_usd": [0, 0],\n'
        '    "confidence": "low|medium|high"\n'
        "  },\n"
        '  "deal_analysis": {\n'
        '    "asking_price_usd": null,\n'
        '    "estimated_profit_usd": null,\n'
        '    "risk_level": "low|medium|high",\n'
        '    "verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP"\n'
        "  },\n"
        '  "assistant_message": ""\n'
        "}\n"
    )

    if hint.strip():
        user += f"\nUser hint: {hint.strip()}\n"

    payload = {
        "model": DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user},
                    {"type": "input_image", "image_url": image_data_url},
                ],
            },
        ],
        "temperature": 0.1,
    }

    r = requests.post(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text}")

    data = r.json()
    out_text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    out_text += c.get("text", "")

    parsed = _try_parse_json(out_text)
    if parsed is not None:
        return parsed

    return {
        "error": "Could not parse model output",
        "raw_text": out_text[:1500],
    }
