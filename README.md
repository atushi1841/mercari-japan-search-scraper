# Mercari Japan Search Scraper

Scrape search results from [Mercari Japan (メルカリ)](https://jp.mercari.com), Japan's largest C2C marketplace. Returns item name, price, condition, brand, thumbnails, and direct item URLs.

## Why this works

Mercari's search page is a fully client-side rendered Next.js app. The underlying search API (`POST https://api.mercari.jp/v2/entities:search`) requires a `DPoP` security header that the page's own JavaScript generates automatically — it cannot be called with plain HTTP requests.

This Actor drives a real headless Chromium browser with Playwright, lets the page issue its API calls, and captures the JSON responses via network interception. **No API keys, no login, no cookies required.**

## Input

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `searchKeyword` | string | CBR250RR | Keyword to search (**required**) |
| `maxItems` | integer | 100 | Max items to collect |
| `maxPages` | number | 10 | Max result pages (~120 items/page) |
| `sort` | select | SORT_SCORE | Relevance / Newest / Recently Updated / Price |
| `priceMin` | integer | 0 | Min price in JPY |
| `priceMax` | integer | 0 | Max price in JPY |
| `proxyConfiguration` | proxy | Apify auto | Proxy for cloud runs |

## Output

One dataset item per listing:

```json
{
  "id": "m92852841305",
  "name": "Honda CBR250RR MC51",
  "price": "7777",
  "status": "ITEM_STATUS_ON_SALE",
  "condition": "5",
  "brand": "Honda",
  "categoryId": "6917",
  "sellerId": "102046822",
  "thumbnail": "https://static.mercdn.net/thumb/item/webp/m92852841305_1.jpg",
  "itemUrl": "https://jp.mercari.com/item/m92852841305",
  "created": "2026-08-13T12:28:34+00:00",
  "updated": "2026-08-13T12:28:34+00:00"
}
```

## Use cases

- **Price research / arbitrage** — monitor what similar items actually sell for
- **Market intelligence** — track inventory levels, condition distribution, and seller activity for a product
- **Reseller sourcing** — find underpriced listings by keyword + price range
- **Brand monitoring** — watch new listings for specific brands

## Pricing

$0.00005 per run + $0.002 per item (100 items ≈ $0.20).

## Limitations

- **Proxy**: Do NOT enable the Apify auto-proxy — it causes page-load timeouts. Mercari works directly from Apify datacenter IPs. If you use a proxy, use residential proxies only.
- Item count is limited by search ranking (Mercari returns up to ~3,400 results per keyword).
- Images are thumbnail URLs; use the `itemUrl` for full details.

## Changelog

- **0.0** — Initial release. Search by keyword, sort, price range; pagination; network-capture based extraction.
