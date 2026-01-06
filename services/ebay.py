#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import base64
import urllib.parse
from time import time
from typing import List, Dict, Any, Optional

import requests

EBAY_CLIENT_ID = (os.getenv("EBAY_CLIENT_ID") or "").strip()
EBAY_CLIENT_SECRET = (os.getenv("EBAY_CLIENT_SECRET") or "").strip()
EBAY_MARKETPLACE_ID = (os.getenv("EBAY_MARKETPLACE_ID") or "EBAY_DE").strip()

EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

_token_cache: Optional[str] = None
_token_expire_at: float = 0.0

def _get_token() -> str:
    global _token_cache, _token_expire_at

    if _token_cache and time() < _token_expire_at:
        return _token_cache

    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET missing")

    creds = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}"
    b64 = base64.b64encode(creds.encode("utf-8")).decode("utf-8")

    headers = {"Authorization": f"Basic {b64}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}

    r = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"eBay token error {r.status_code}: {r.text}")

    payload = r.json()
    token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 7200))
    if not token:
        raise RuntimeError("eBay token missing access_token")

    _token_cache = token
    _token_expire_at = time() + max(60, expires_in - 60)
    return token

def ebay_search_comps(query: str, want: int = 5, limit: int = 12) -> List[Dict[str, Any]]:
    token = _get_token()

    params = {
        "q": query,
        "limit": str(limit),
        "filter": "buyingOptions:{FIXED_PRICE|AUCTION}",
    }
    url = EBAY_BROWSE_SEARCH_URL + "?" + urllib.parse.urlencode(params, safe=":{}|")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
        "Accept": "application/json",
    }

    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"eBay search error {r.status_code}: {r.text}")

    data = r.json() or {}
    items = data.get("itemSummaries", []) or []

    out = []
    for it in items[:want]:
        price = it.get("price") or {}
        loc = it.get("itemLocation") or {}
        out.append({
            "title": it.get("title") or "—",
            "url": it.get("itemWebUrl") or "",
            "price_value": price.get("value"),
            "price_currency": price.get("currency"),
            "country": loc.get("country") or loc.get("countryCode") or "—",
            "condition": it.get("condition") or "—",
        })

    return [x for x in out if x["url"]]
