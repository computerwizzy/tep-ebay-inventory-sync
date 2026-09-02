# TEP eBay Inventory Sync — AI Context

## What this repo does
Reads current inventory quantities from the Tires and Engine Performance (TEP) Shopify store and pushes them to that store's active eBay listings. Copied from `ebay-inventory-sync` (the Rines & Wheels version) — same logic, different store. Shopify is the source of truth, eBay is the destination.

## Data flow
```
Shopify (TEP store, all variants + inventoryQuantity) → eBay listings (ReviseFixedPriceItem /
                                                          EndFixedPriceItem / RelistFixedPriceItem)
```

## Schedule
Runs via GitHub Actions every 4 hours (`.github/workflows/ebay_sync.yml`), plus manual
`workflow_dispatch`. Repo: https://github.com/computerwizzy/tep-ebay-inventory-sync (private).

## Key script
`scripts/sync_ebay_inventory.py`

## eBay accounts
`EBAY_ACCOUNTS` in the script lists which eBay seller accounts to sync, all under the same
developer keyset (`EBAY_APP_ID`/`EBAY_DEV_ID`/`EBAY_CERT_ID`). Currently only `wheelstires` is
active; `saleswbrteam` is present but commented out.

## Important behavior
- Quantities pushed to eBay are capped per SKU by `max_qty_for_sku()`: SKUs containing
  `SET4-` (truck-fitment sets) are capped at **1**; everything else at **4**.
- Handles both multi-variation and single-variation eBay listings (`is_variation` flag from
  `GetSellerList`'s `<Variations>` block).
- A single-SKU (non-variation) listing can't be revised to Quantity=0 directly, so when its
  Shopify stock hits 0 the script **ends** it (`EndFixedPriceItem`) instead, and records it in
  `data/ended_listings.json` (per account) so a later run can find it again.
- On each run, before syncing active listings, the script checks `data/ended_listings.json`
  for any previously-ended SKU that's back in stock and **relists** it (`RelistFixedPriceItem`).
  Only listings this script itself ended are eligible — nothing else ended (manually, by eBay,
  etc.) is auto-revived. `RelistFixedPriceItem` enforces that the request's SiteID header match
  the listing's original site (wheel/tire listings are often under `eBayMotors`, SiteID 100, not
  the default `US`/0) — the script looks this up per item via `GetItem` before relisting.
  If eBay rejects the relist because an identical listing already exists (ErrorCode 21919067 —
  something else already relisted it), the entry is just dropped from tracked state.
- The GitHub Actions workflow commits `data/ended_listings.json` back to the repo after each
  run (`git add`/`commit`/`push` with `[skip ci]`) so this state survives across the ephemeral
  runner. Running the script locally will update the local file but won't push it — commit that
  yourself if you want a local run's ended/relisted state to persist for the next run.
- `GetSellerList` (used to find active listings) is backed by a search index that can lag
  hours behind brand-new listings — `GetMyeBaySelling`'s `ActiveList` is the authoritative
  live count if the two ever disagree.
- Matches by SKU between Shopify variants and eBay variation SKUs. A multi-variation eBay
  listing's variation SKUs (e.g. a `-TPMS` / `-LUGNUTS` free-gift choice) need an exact-matching
  SKU string in Shopify to sync — the script doesn't infer or strip suffixes.

## eBay API details
- Uses Trading API (XML) via `https://api.ebay.com/ws/api.dll`
- Calls used: `GetSellerList`, `GetItem`, `ReviseFixedPriceItem`, `EndFixedPriceItem`,
  `RelistFixedPriceItem`
- The Auth'n'Auth `EBAY_USER_TOKEN*` values are per-seller-account tokens generated via
  developer.ebay.com's "Get a Token from eBay via Your Application" flow (RuName-based sign-in);
  they're distinct from the shared `EBAY_APP_ID`/`EBAY_DEV_ID`/`EBAY_CERT_ID` developer keyset,
  which is reusable across any number of seller accounts.

## Shopify details
- Store: `tires-and-engine-performance.myshopify.com`
- Reads `inventoryQuantity` from all product variants

## Environment variables required
```
EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID
EBAY_USER_TOKEN, EBAY_USER_TOKEN_WHEELSTIRES   # one per account in EBAY_ACCOUNTS
SHOPIFY_STORE_URL, SHOPIFY_ACCESS_TOKEN
```
Set locally in `.env` (gitignored); on GitHub Actions these are repo secrets, injected via the
workflow's `env:` block.

## Related repos in this ecosystem
See `ebay-inventory-sync` (Rines & Wheels version) for the sibling repos that feed Shopify inventory from suppliers (ATD, AutoSync, WheelPros, etc.).
