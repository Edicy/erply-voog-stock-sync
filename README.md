# Erply ↔ Voog Stock Sync (POC)

A tiny CLI to keep products in sync between Erply (retail back office) and Voog (website + ecommerce).

What it does
- Erply → Voog: mirror Erply stock by SKU to Voog
- Voog → Erply: push absolute stock back to Erply (registration/write‑off)
- Webhook server stub: ready for future Voog → Erply order sync
- Field ownership:
  - Stock: two‑way
  - Status (ACTIVE/live): two‑way
  - Price: excluded (see caveats)
  - Name/SKU: Erply → Voog
  - SEO, description, images: Voog‑only

Proven
- Live‑tested with multiple SKUs
- CSV → Erply import (SKU auto‑generation) → full sync loop

⸻

## Quick start

1) Install deps

```
pip install -r requirements.txt
```

2) Environment

Required
- ERPLY_CLIENT_CODE
- ERPLY_USERNAME
- ERPLY_PASSWORD
- VOOG_SITE          # e.g. mysite for https://mysite.voog.com
- VOOG_API_TOKEN     # Voog API token with write access

Optional
- ERPLY_API_URL=https://<client_code>.erply.com/api/   # default
- ERPLY_WAREHOUSE_ID=<int>
- SUM_ALL_WAREHOUSES=true|false   # default: true

⸻

## Commands

Erply → Voog (one SKU)
```
python erply_voog_sync.py sync --sku ABC123 -v
```

Voog → Erply (set absolute stock)
```
python erply_voog_sync.py erply-set-stock --sku ABC123 --stock 8 -v
```

Create product in Erply
```
python erply_voog_sync.py erply-create-product --sku ABC123 --name "Close shave"
```

Run webhook server
```
python erply_voog_sync.py serve --port 8089
```

Multi‑field sync (same repo)

Script: v2/erply_voog_sync_v2.py

Erply → Voog (price excluded)
```
python v2/erply_voog_sync_v2.py   --sku ABC123   --direction erply-to-voog   --include-stock   --no-include-price   --include-status   --include-sku-name   -v
```

Voog → Erply (price excluded)
```
python v2/erply_voog_sync_v2.py   --sku ABC123   --direction voog-to-erply   --include-stock   --no-include-price   --include-status   --no-include-sku-name   -v
```

⸻

## What’s Voog? What’s Erply?
- Voog: website & ecommerce platform for building and selling online.
- Erply: retail back office for products, stock, POS, and reports.

⸻

## Getting started (accounts & credentials)

Erply
- Create an account (trial works).
- Note your clientCode, username, password.
- API flow: verifyUser → sessionKey (used on each request).

Voog
- Generate an API token in Admin: Account → My profile → API token. Use header `X-API-TOKEN: <token>`.
- Docs: https://voog.com/developers/api
- Your site base URL: https://<site>.voog.com

Recommended
- Set Erply base currency to EUR; decide VAT (POC used 24%).

⸻

## Notes
- Voog write auth: X-API-TOKEN header
- Erply auth: verifyUser → sessionKey
- Stock read: getProductStock → amountInStock
- Write‑off: requires reasonID (POC uses 1)

⸻

## Caveats
- Prices: some Erply accounts need price lists or specific configuration. If reads return 0/null, treat Voog as the price source and run with `--no-include-price`.
- Variants: skipped in POC (parents only).
- Images: uploading to Erply may require CDN permissions; disabled in POC.

License: MIT (POC). No secrets committed.
