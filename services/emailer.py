import os
import requests

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
FROM_EMAIL = os.getenv("FROM_EMAIL", "Flea Assistant <no-reply@example.com>").strip()

def send_email(to_email: str, subject: str, text: str) -> tuple[bool, str]:
    to_email = (to_email or "").strip()
    if not to_email:
        return False, "EMPTY_EMAIL"

    if not RESEND_API_KEY:
        print("=== EMAIL (log only) ===")
        print("TO:", to_email)
        print("SUBJECT:", subject)
        print(text)
        print("========================")
        return True, "LOG_ONLY"

    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to_email], "subject": subject, "text": text},
            timeout=15,
        )
        if 200 <= r.status_code < 300:
            return True, "SENT"
        return False, f"RESEND_ERROR_{r.status_code}:{r.text[:200]}"
    except Exception as e:
        return False, f"RESEND_EXCEPTION:{str(e)}"
