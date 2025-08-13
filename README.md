# Erply ↔ Voog Stock Sync (POC)

Tiny CLI to keep products in sync between Erply (retail back office) and Voog (website + ecommerce).

- Erply → Voog: mirror Erply stock by SKU to Voog
- Voog → Erply: set Erply absolute stock to match Voog (registration/write-off)
- Webhook server stub for future order events

Field ownership (POC)
- Stock: two‑way
- Status: two‑way (ACTIVE/live)
- Price: excluded (see caveat)
- Name/SKU: Erply → Voog
- SEO/description/images: Voog‑only

Proven in live tests
- Stock and status sync both ways for multiple SKUs
- CSV → Erply import (SKU auto‑generation), then full loop

## Quick start

1) Install deps

```
pip install -r requirements.txt
```

2) Environment

Required:
- ERPLY_CLIENT_CODE
- ERPLY_USERNAME
- ERPLY_PASSWORD
- VOOG_SITE (e.g. `mysite` for `https://mysite.voog.com`)
- VOOG_API_TOKEN (Voog Admin API token with write access)

Optional:
- ERPLY_API_URL (default `https://<client_code>.erply.com/api/`)
- ERPLY_WAREHOUSE_ID (int)
- SUM_ALL_WAREHOUSES=true|false (default true)

3) Commands

- Sync Erply → Voog for one SKU:
```
python erply_voog_sync.py sync --sku ABC123 -v
```

- Voog → Erply: set absolute stock in Erply to a value (uses registration/write-off under the hood):
```
python erply_voog_sync.py erply-set-stock --sku ABC123 --stock 8 -v
```

- Create minimal product in Erply:
```
python erply_voog_sync.py erply-create-product --sku ABC123 --name "Close shave"
```

- Run webhook server (for future Voog → Erply order events):
```
python erply_voog_sync.py serve --port 8089
```

### v2 (multi‑field sync)
- Script: `v2/erply_voog_sync_v2.py`
- Example (Erply → Voog, price excluded):
```
python v2/erply_voog_sync_v2.py --sku ABC123 --direction erply-to-voog --include-stock --no-include-price --include-status --include-sku-name -v
```
- Example (Voog → Erply, price excluded):
```
python v2/erply_voog_sync_v2.py --sku ABC123 --direction voog-to-erply --include-stock --no-include-price --include-status --no-include-sku-name -v
```

### What’s Voog? What’s Erply?
- Voog: website & ecommerce platform for building and selling online.
- Erply: retail back office (products, stock, POS, reports).

Getting started (accounts & creds)
- Erply: create an account (trial OK), note `clientCode`, username, password. The API flow uses `verifyUser` to obtain a `sessionKey`.
- Voog: get an Admin API token (`X-API-TOKEN`) from the site admin; site is addressed as `https://<site>.voog.com`.
- Recommended: set Erply base currency to EUR and decide VAT (POC uses 24%).

Notes:
- Voog write auth uses header `X-API-TOKEN`.
- Erply auth uses `verifyUser` to get `sessionKey`.
- Erply stock read uses `getProductStock` (`amountInStock`).
- Write-off requires `reasonID` (POC uses `1`).

Caveats
- Prices: some Erply accounts require price list configuration. If reads return 0/null, treat Voog as the price source and run with `--no-include-price`.
- Variants: skipped (parents only).
- Images: uploading to Erply may require CDN permissions; disabled in POC.

License: MIT (POC). No secrets committed.
