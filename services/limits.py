from dataclasses import dataclass
from datetime import datetime, timezone
import db

FREE_TOTAL_LIMIT = 5
EMAIL_DAILY_LIMIT = 10
PAID_DAILY_LIMIT = 200

def utc_day_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

@dataclass
class LimitStatus:
    plan: str
    allowed: bool
    reason: str
    remaining: int
    limit: int

def compute_plan(device_id: str) -> str:
    if db.is_device_paid(device_id):
        return "paid"
    if db.get_email_for_device(device_id):
        return "email"
    return "free"

def check_limit(device_id: str) -> LimitStatus:
    plan = compute_plan(device_id)
    day = utc_day_str()

    if plan == "paid":
        used = db.get_daily_count(device_id, day)
        limit = PAID_DAILY_LIMIT
        remaining = max(0, limit - used)
        return LimitStatus(plan, remaining > 0, "OK" if remaining > 0 else "DAILY_LIMIT_REACHED", remaining, limit)

    if plan == "email":
        used = db.get_daily_count(device_id, day)
        limit = EMAIL_DAILY_LIMIT
        remaining = max(0, limit - used)
        return LimitStatus(plan, remaining > 0, "OK" if remaining > 0 else "DAILY_LIMIT_REACHED", remaining, limit)

    total = db.get_total_count(device_id)
    limit = FREE_TOTAL_LIMIT
    remaining = max(0, limit - total)
    return LimitStatus(plan, remaining > 0, "OK" if remaining > 0 else "FREE_LIMIT_REACHED", remaining, limit)

def register_usage(device_id: str):
    plan = compute_plan(device_id)
    day = utc_day_str()
    if plan in ("paid", "email"):
        db.inc_daily_count(device_id, 1, day)
    else:
        db.inc_total_count(device_id, 1)
