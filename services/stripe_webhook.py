import os
import secrets
import stripe

import db
from services.emailer import send_email

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "Flea Assistant Pro ($10)").strip()

def _make_license_key() -> str:
    return "FA-" + secrets.token_hex(8).upper()

def handle_stripe_webhook(payload: bytes, sig_header: str | None) -> tuple[int, dict]:
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        return 503, {"ok": False, "error": "Stripe not configured (missing STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET)"}

    stripe.api_key = STRIPE_SECRET_KEY

    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return 400, {"ok": False, "error": f"Webhook signature error: {str(e)}"}

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email") or session.get("customer_email")
        if not email:
            return 200, {"ok": True, "note": "No email in session"}

        license_key = _make_license_key()
        db.create_license(license_key=license_key, email=email, plan="paid")

        subject = "Your Flea Assistant Pro key"
        text = f"""Thanks for purchasing {PRODUCT_NAME}!

Your license key:
{license_key}

How to activate (1 device):
1) Open the app
2) Paste the license key in the “Activate Pro” field
3) Done — Pro is now bound to this device

— Flea Assistant
"""
        ok, status = send_email(email, subject, text)
        return 200, {"ok": True, "created_license": True, "email_status": status}

    return 200, {"ok": True, "ignored": event["type"]}
