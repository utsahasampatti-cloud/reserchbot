import base64
import os
from typing import List, Optional, Dict, Any

from openai import OpenAI

# Один клієнт на весь процес
_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _b64_data_url(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{content_type};base64,{b64}"


def vision_quick_sniff(
    images: List[Dict[str, Any]],
    hint: Optional[str] = None,
    asking_price: Optional[float] = None,
    currency: str = "USD",
    language: str = "en",
) -> str:
    """
    images: list of { "data": bytes, "content_type": "image/jpeg" }
    returns: raw text from model (we'll parse JSON outside if you do)
    """

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing in environment variables")

    # Build image parts for Responses API
    image_parts = []
    for img in images:
        data_url = _b64_data_url(img["data"], img.get("content_type") or "image/jpeg")
        image_parts.append({"type": "input_image", "image_url": data_url})

    # Keep prompt tight and deterministic
    user_context = []
    if hint:
        user_context.append(f"Hint: {hint}")
    if asking_price is not None:
        user_context.append(f"Asking price: {asking_price} {currency}")
    user_context.append(f"Language: {language}")

    prompt = (
        "Return ONLY valid JSON. No markdown.\n"
        "You are Treasure Sniffer: a conservative resale assistant for EU flea markets.\n"
        "Analyze the item from the photos, estimate resale range, risk, and verdict.\n"
        "Be conservative. If unsure, widen range and lower confidence.\n"
        "If asking price is provided, incorporate it into the verdict.\n"
        "Output JSON shape:\n"
        "{\n"
        '  "ui": {\n'
        '    "fields": {\n'
        '      "Item": "...",\n'
        '      "Condition": "...",\n'
        '      "Resale Price Range": "$low - $high",\n'
        '      "Confidence": "low|medium|high",\n'
        '      "Risk Level": "low|medium|high",\n'
        '      "Verdict": "BUY|BUY IF NEGOTIATED LOWER|SKIP"\n'
        "    },\n"
        '    "summary": "short practical advice"\n'
        "  }\n"
        "}\n"
    )

    content = [{"type": "input_text", "text": prompt + "\n" + "\n".join(user_context)}] + image_parts

    resp = _client.responses.create(
        model="gpt-4o-mini",
        input=[{"role": "user", "content": content}],
    )

    # Responses API returns text in output_text helper
    return resp.output_text
