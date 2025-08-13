# Erply ↔ Voog Stock Sync (POC)

Minimal CLI and webhook server to keep product stock in sync between Erply and Voog.

- One-way: Erply → Voog by SKU
- Two-way POC: Voog change → set Erply absolute stock (registration/write-off)
- Webhook server stub for order events

Proven in a live test:
- Set Erply stock to 10 → mirrored to Voog (bulk update) OK
- Changed Voog stock to 8 → mirrored back to Erply (write-off with reasonID) OK

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

- Set absolute stock in Erply to match a value (registration/write-off):
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

Notes:
- Voog write auth uses header `X-API-TOKEN`.
- Erply auth uses `verifyUser` to get `sessionKey`.
- Erply stock read uses `getProductStock` (`amountInStock`).
- Write-off requires `reasonID` (POC uses `1`).

License: MIT (POC). No secrets committed.
