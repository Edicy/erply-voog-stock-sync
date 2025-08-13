# v2 POC

Expands sync to multiple fields:
- Two‑way: stock, price, status
- Erply → Voog: SKU, name
- Voog‑only: SEO/description/images (no overwrite)

Usage:
```
pip install -r ../requirements.txt
python erply_voog_sync_v2.py sync_fields --sku ABC123 --direction both -v
```

Notes:
- Uses Voog `X-API-TOKEN` and Erply `verifyUser`.
- Still POC: no variants/categories/images sync.
