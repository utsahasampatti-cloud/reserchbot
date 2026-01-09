from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, TimeoutError as PWTimeout


PRICE_RE = re.compile(r"([0-9]+(?:[\.,][0-9]+)?)")


def _parse_price_to_usd(raw: str) -> Optional[float]:
    if not raw:
        return None
    s = raw.replace(",", "").strip()
    m = PRICE_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _min_max(prices: List[float]) -> Optional[Tuple[float, float]]:
    if not prices:
        return None
    return (round(min(prices), 2), round(max(prices), 2))


async def _search_ebay(query: str, sold: bool, limit: int = 8, timeout_ms: int = 25000) -> Dict[str, Any]:
    base = "https://www.ebay.com/sch/i.html"
    q = query.strip().replace(" ", "+")
    url = f"{base}?_nkw={q}"
    if sold:
        url += "&LH_Sold=1&LH_Complete=1"

    examples: List[Dict[str, Any]] = []
    prices: List[float] = []
    count_text: Optional[str] = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="en-US")
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            try:
                el = await page.query_selector("h1.srp-controls__count-heading span.BOLD")
                if el:
                    count_text = (await el.inner_text()).strip()
                else:
                    el2 = await page.query_selector("h1.srp-controls__count-heading")
                    if el2:
                        count_text = (await el2.inner_text()).strip()
            except Exception:
                pass

            items = await page.query_selector_all("li.s-item")
            for it in items:
                if len(examples) >= limit:
                    break

                title_el = await it.query_selector("div.s-item__title span[role='heading']")
                if not title_el:
                    continue
                title = (await title_el.inner_text()).strip()
                if not title or title.lower() in {"shop on ebay"}:
                    continue

                link_el = await it.query_selector("a.s-item__link")
                href = await link_el.get_attribute("href") if link_el else None
                if not href:
                    continue

                price_el = await it.query_selector("span.s-item__price")
                raw_price = (await price_el.inner_text()).strip() if price_el else ""
                price = _parse_price_to_usd(raw_price)

                sold_date = None
                if sold:
                    sd = await it.query_selector("span.s-item__ended-date")
                    if sd:
                        sold_date = (await sd.inner_text()).strip()

                ex = {"title": title, "url": href, "raw_price": raw_price, "price_usd": price}
                if sold_date:
                    ex["sold_date"] = sold_date

                examples.append(ex)
                if isinstance(price, (int, float)):
                    prices.append(float(price))

        except PWTimeout:
            pass
        finally:
            await context.close()
            await browser.close()

    mm = _min_max(prices)
    return {
        "query": query,
        "mode": "sold" if sold else "active",
        "count_text": count_text,
        "price_range_usd": list(mm) if mm else None,
        "examples": examples,
    }


async def ebay_scout(queries: List[str], limit_each: int = 6) -> Dict[str, Any]:
    queries = [q.strip() for q in queries if q and q.strip()][:3]

    active: List[Dict[str, Any]] = []
    sold: List[Dict[str, Any]] = []
    sold_prices: List[float] = []

    for q in queries:
        a = await _search_ebay(q, sold=False, limit=limit_each)
        s = await _search_ebay(q, sold=True, limit=limit_each)
        active.append(a)
        sold.append(s)
        for ex in s.get("examples", []):
            p = ex.get("price_usd")
            if isinstance(p, (int, float)):
                sold_prices.append(float(p))

    overall = _min_max(sold_prices)
    return {
        "platform": "ebay",
        "queries_used": queries,
        "active": active,
        "sold": sold,
        "overall_sold_price_range_usd": list(overall) if overall else None,
        "note": "Sold listings are the primary market signal.",
    }
