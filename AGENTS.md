# TEP eBay Inventory Sync — AI Context

## What this repo does
Reads current inventory quantities from the Tires and Engine Performance (TEP) Shopify store and pushes them to that store's active eBay listings. Copied from `ebay-inventory-sync` (the Rines & Wheels version) — same logic, different store. Shopify is the source of truth, eBay is the destination.

## Data flow
```
Shopify (TEP store, all variants + inventoryQuantity) → eBay listings (ReviseFixedPriceItem)
```

## Schedule
Not yet scheduled. The original repo runs via GitHub Actions every 4 hours (`.github/workflows/ebay_sync.yml`) — add an equivalent workflow here once this repo is pushed to GitHub.

## Key script
`scripts/sync_ebay_inventory.py`

## Important behavior
- Quantities pushed to eBay are **capped at 4** regardless of actual Shopify stock
- Handles both multi-variation and single-variation eBay listings
- Uses `GetSellerList` (GTC window = now+32 days) to find all active listings
- Matches by SKU between Shopify variants and eBay variation SKUs

## eBay API details
- Uses Trading API (XML) via `https://api.ebay.com/ws/api.dll`
- Calls used: `GetSellerList`, `ReviseFixedPriceItem`
- `EBAY_*` credentials were copied from the wheels repo (same eBay seller account listing across product lines) — confirm this is correct before running.

## Shopify details
- Store: **TODO** — fill in the TEP `.myshopify.com` domain in `.env`
- Reads `inventoryQuantity` from all product variants

## Environment variables required
```
EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, EBAY_USER_TOKEN
SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN
```
`.env` in this repo has placeholder values for `SHOPIFY_STORE_URL` / `SHOPIFY_ACCESS_TOKEN` — replace with the TEP store's own admin API token before running.

## Related repos in this ecosystem
See `ebay-inventory-sync` (Rines & Wheels version) for the sibling repos that feed Shopify inventory from suppliers (ATD, AutoSync, WheelPros, etc.).
