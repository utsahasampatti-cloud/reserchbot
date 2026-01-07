import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "app.db")

def _utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_total (
            device_id TEXT PRIMARY KEY,
            total_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_daily (
            device_id TEXT NOT NULL,
            day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (device_id, day)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS device_email (
            device_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'paid',
            device_id TEXT,
            created_at TEXT NOT NULL,
            bound_at TEXT
        )
        """)

def get_email_for_device(device_id: str) -> str | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT email FROM device_email WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        return row[0] if row else None

def set_email_for_device(device_id: str, email: str):
    now = datetime.now(timezone.utc).isoformat()
    email = (email or "").strip().lower()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO device_email(device_id, email, verified, updated_at)
            VALUES(?,?,1,?)
            ON CONFLICT(device_id) DO UPDATE SET
              email=excluded.email,
              verified=1,
              updated_at=excluded.updated_at
        """, (device_id, email, now))

def get_total_count(device_id: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT total_count FROM usage_total WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0

def inc_total_count(device_id: str, amount: int = 1):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO usage_total(device_id, total_count, created_at)
            VALUES(?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET total_count = total_count + ?
        """, (device_id, amount, now, amount))

def get_daily_count(device_id: str, day: str | None = None) -> int:
    day = day or _utc_date_str()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count FROM usage_daily WHERE device_id=? AND day=?", (device_id, day))
        row = cur.fetchone()
        return int(row[0]) if row else 0

def inc_daily_count(device_id: str, amount: int = 1, day: str | None = None):
    day = day or _utc_date_str()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO usage_daily(device_id, day, count)
            VALUES(?,?,?)
            ON CONFLICT(device_id, day) DO UPDATE SET count = count + ?
        """, (device_id, day, amount, amount))

def create_license(license_key: str, email: str, plan: str = "paid"):
    now = datetime.now(timezone.utc).isoformat()
    email = (email or "").strip().lower()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO licenses(license_key, email, plan, device_id, created_at, bound_at)
            VALUES(?,?,?,?,?,NULL)""", (license_key, email, plan, None, now))

def bind_license_to_device(license_key: str, device_id: str) -> tuple[bool, str]:
    now = datetime.now(timezone.utc).isoformat()
    license_key = (license_key or "").strip().upper()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT device_id FROM licenses WHERE license_key=?", (license_key,))
        row = cur.fetchone()
        if not row:
            return False, "LICENSE_NOT_FOUND"
        bound_device = row[0]
        if bound_device and bound_device != device_id:
            return False, "LICENSE_ALREADY_BOUND_TO_ANOTHER_DEVICE"
        if bound_device == device_id:
            return True, "ALREADY_BOUND"
        cur.execute("UPDATE licenses SET device_id=?, bound_at=? WHERE license_key=?", (device_id, now, license_key))
        return True, "BOUND_OK"

def is_device_paid(device_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM licenses WHERE device_id=? AND plan='paid' LIMIT 1", (device_id,))
        return cur.fetchone() is not None
