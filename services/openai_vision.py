import base64
from typing import List, Optional

from openai import OpenAI


def _b64_data_url(img_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def vision_quick_sniff(
    openai_images: List[dict],
    prompt: str,
    model: str = "gpt-4o-mini",
    max_output_tokens: int = 600,
) -> str:
    """
    openai_images: list of {"type":"input_image","image_url":"data:..."}
    Returns: raw text from model
    """
    client = OpenAI()

    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *openai_images,
                ],
            }
        ],
        max_output_tokens=max_output_tokens,
    )

    # Combine text output safely
    out = []
    for item in getattr(resp, "output", []) or []:
        for c in getattr(item, "content", []) or []:
            t = getattr(c, "text", None)
            if t:
                out.append(t)
    return "\n".join(out).strip()
