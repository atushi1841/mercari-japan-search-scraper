"""
Mercari Japan Search Scraper — Apify Actor.

Strategy: Mercari's web search is fully client-side rendered (Next.js).
The search API (POST https://api.mercari.jp/v2/entities:search) requires a
DPoP header that the page's own JavaScript generates automatically.
Therefore we drive a real (headless) browser with Playwright, let the page
issue its API calls, and capture the JSON responses via page.on('response').

Pagination: the page renders a "次へ" (next) link carrying ?page_token=...
Clicking it makes the page issue the next entities:search call, which we
capture the same way.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode

from playwright.async_api import async_playwright

try:
    from apify import Actor
except Exception:  # pragma: no cover - local dev without apify SDK
    Actor = None

SEARCH_API_MARKER = "/v2/entities:search"
SEARCH_BASE = "https://jp.mercari.com/search"
START_URL = SEARCH_BASE + "?{params}"


def _fmt_ts(unix_ts) -> str:
    """Unix seconds -> ISO 8601 UTC string (or '' if invalid)."""
    try:
        return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _item_to_output(it: dict) -> dict:
    """Map a raw API item to the output schema fields."""
    item_id = it.get("id")
    photos = it.get("photos") or []
    thumbs = it.get("thumbnails") or []
    thumb = thumbs[0] if thumbs else (photos[0].get("uri", "") if photos else "")
    brand = (it.get("itemBrand") or {}).get("name", "") if isinstance(it.get("itemBrand"), dict) else ""
    return {
        "id": item_id or "",
        "name": it.get("name", ""),
        "price": str(it.get("price", "")),
        "status": it.get("status", ""),
        "condition": str(it.get("itemConditionId", "")),
        "brand": brand,
        "categoryId": str(it.get("categoryId", "")),
        "sellerId": str(it.get("sellerId", "")),
        "thumbnail": thumb,
        "itemUrl": f"https://jp.mercari.com/item/{item_id}" if item_id else "",
        "created": _fmt_ts(it.get("created")),
        "updated": _fmt_ts(it.get("updated")),
    }


def _build_search_url(keyword: str, sort: str, price_min: int, price_max: int) -> str:
    params = {"keyword": keyword}
    if sort and sort != "SORT_SCORE":
        params["sort"] = sort
    if price_min and price_min > 0:
        params["min_price"] = price_min
    if price_max and price_max > 0:
        params["max_price"] = price_max
    return START_URL.format(params=urlencode(params))


async def _run(user_input: dict) -> None:
    use_actor = Actor is not None and Actor.is_at_home()

    keyword = (user_input.get("searchKeyword") or "").strip()
    if not keyword:
        msg = "Missing searchKeyword in actor input"
        if use_actor:
            await Actor.fail(status_message=msg)
        else:
            print(msg)
        return

    max_items = int(user_input.get("maxItems", 100))
    max_pages = int(user_input.get("maxPages", 10))
    sort = user_input.get("sort", "SORT_SCORE")
    price_min = int(user_input.get("priceMin", 0) or 0)
    price_max = int(user_input.get("priceMax", 0) or 0)

    collected = 0
    page_no = 0
    url = _build_search_url(keyword, sort, price_min, price_max)

    proxy_url = None
    if use_actor:
        proxy_cfg = await Actor.create_proxy_configuration(
            actor_proxy_input=user_input.get("proxyConfiguration")
        )
        if proxy_cfg:
            proxy_url = await proxy_cfg.new_url()
            Actor.log.info(f"Using Apify proxy: {proxy_url[:40]}...")

    browser = None
    try:
        async with async_playwright() as pw:
            # headless=False works under xvfb
            browser = await pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                proxy={"server": proxy_url} if proxy_url else None,
            )
            page = await browser.new_page(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/151.0.0.0 Safari/537.36"
                ),
                locale="ja-JP",
            )

            capture_q: asyncio.Queue = asyncio.Queue()

            async def on_console(msg):
                if use_actor:
                    Actor.log.info(f"[console] {msg.type}: {msg.text}")
                else:
                    print(f"[console] {msg.type}: {msg.text}")
            page.on("console", lambda msg: asyncio.create_task(on_console(msg)))

            async def on_request(req):
                if "api.mercari.jp" in req.url:
                    if use_actor:
                        Actor.log.info(f"[request] {req.method} {req.url}")
                    else:
                        print(f"[request] {req.method} {req.url}")
            page.on("request", lambda req: asyncio.create_task(on_request(req)))

            async def on_response(resp):
                if SEARCH_API_MARKER not in resp.url:
                    return
                if use_actor:
                    Actor.log.info(f"Search API response: {resp.status} {resp.url}")
                    if resp.status != 200:
                        Actor.log.warning(f"Search API HTTP {resp.status} for {resp.url}")
                try:
                    data = await resp.json()
                except Exception as e:
                    if use_actor:
                        Actor.log.warning(f"Failed to parse JSON from {resp.url}: {e}")
                    return
                await capture_q.put(data)

            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            async def drain(q: asyncio.Queue, timeout: float = 10.0):
                """Drain queued search responses; return list of item dicts."""
                items = []
                try:
                    while True:
                        data = await asyncio.wait_for(q.get(), timeout=timeout)
                        for it in data.get("items") or []:
                            if it.get("id"):  # skip ad placeholders
                                items.append(_item_to_output(it))
                except asyncio.TimeoutError:
                    pass
                return items

            while collected < max_items and page_no < max_pages:
                page_no += 1
                if use_actor:
                    Actor.log.info(f"Fetching page {page_no}: {url[:120]}")
                else:
                    print(f"[INFO] Fetching page {page_no}: {url[:120]}")

                # Navigate using full page load (page.goto) for every page
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                # Wait for the first search API response, then drain extras
                page_items = []
                try:
                    first = await asyncio.wait_for(capture_q.get(), timeout=30)
                    page_items.extend(
                        _item_to_output(it) for it in (first.get("items") or []) if it.get("id")
                    )
                    page_items.extend(await drain(capture_q, timeout=3.0))
                except asyncio.TimeoutError:
                    if use_actor:
                        Actor.log.warning(f"No search API response on page {page_no}; trying scroll...")
                    else:
                        print(f"[WARN] No search API response on page {page_no}; trying scroll...")
                    try:
                        for _ in range(3):
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(0.5)
                    except Exception as e:
                        if use_actor:
                            Actor.log.warning(f"Scroll failed: {e}")
                        else:
                            print(f"[WARN] Scroll failed: {e}")
                    try:
                        first = await asyncio.wait_for(capture_q.get(), timeout=10)
                        page_items.extend(
                            _item_to_output(it) for it in (first.get("items") or []) if it.get("id")
                        )
                        page_items.extend(await drain(capture_q, timeout=3.0))
                    except asyncio.TimeoutError:
                        if use_actor:
                            Actor.log.warning(f"No search API response after scroll on page {page_no}")
                            try:
                                item_count = await page.evaluate("document.querySelectorAll(\"a[href*='/item/']\").length")
                                current_href = await page.evaluate("location.href")
                                Actor.log.warning(f"Page {page_no} items (SSR)={item_count} href={current_href}")
                            except Exception as e:
                                Actor.log.warning(f"Could not get SSR info: {e}")
                        else:
                            print(f"[WARN] No search API response after scroll on page {page_no}")
                        break

                for item in page_items:
                    if collected >= max_items:
                        break
                    if use_actor:
                        await Actor.push_data(item)
                    else:
                        print(json.dumps(item, ensure_ascii=False))
                    collected += 1

                if collected >= max_items or page_no >= max_pages:
                    break

                # Find next page link (次へ) to follow pagination
                next_url = await page.evaluate(
                    """() => {
                        const anchors = [...document.querySelectorAll('a')];
                        const n = anchors.find(a => (a.textContent || '').trim() === '次へ');
                        return n ? n.href : null;
                    }"""
                )
                if not next_url:
                    if use_actor:
                        Actor.log.info("No 'next' link found; pagination ended")
                    else:
                        print("[INFO] No 'next' link found; pagination ended")
                    break
                url = next_url

            if use_actor:
                Actor.log.info(f"Done. Collected {collected} items from {page_no} page(s).")
            else:
                print(f"[INFO] Done. Collected {collected} items from {page_no} page(s).")
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def main() -> None:
    actor_input = {}
    if Actor is not None:
        async with Actor:
            actor_input = await Actor.get_input() or {}
            await _run(actor_input)
    else:
        raw = sys.stdin.read().strip()
        if raw:
            actor_input = json.loads(raw)
        await _run(actor_input)


if __name__ == "__main__":
    asyncio.run(main())
