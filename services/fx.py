#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import statistics
from typing import Dict, Any, List, Optional, Tuple

import requests

FX_URL = "https://api.frankfurter.dev/latest"
_FX_CACHE: Dict[Tuple[str, str], float] = {}

def fx_rate(frm: str, to: str = "USD") -> Optional[float]:
    frm = (frm or "").upper().strip()
    to = (to or "").upper().strip()
    if not frm or not to:
        return None
    if frm == to:
        return 1.0

    key = (frm, to)
    if key in _FX_CACHE:
        return _FX_CACHE[key]

    try:
        r = requests.get(FX_URL, params={"from": frm, "to": to}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        rate = float(data["rates"][to])
        _FX_CACHE[key] = rate
        return rate
    except Exception:
        return None

def enrich_prices_usd(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        v = it.get("price_value")
        cur = (it.get("price_currency") or "").upper().strip()
        usd = None
        original = None

        try:
            fv = float(v)
            original = f"{fv:.2f} {cur}" if cur else f"{fv:.2f}"
            rate = fx_rate(cur, "USD") if cur else None
            if rate is not None:
                usd = fv * rate
        except Exception:
            pass

        out.append({**it, "price_original": original, "price_usd": usd})
    return out

def average_usd(items: List[Dict[str, Any]]) -> Optional[float]:
    prices = []
    for it in items:
        usd = it.get("price_usd")
        if isinstance(usd, (int, float)):
            prices.append(float(usd))
    if not prices:
        return None
    return statistics.mean(prices)
