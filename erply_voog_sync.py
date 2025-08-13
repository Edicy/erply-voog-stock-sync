import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests
import typer
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
import uvicorn


app = typer.Typer(help="Erply ↔ Voog stock sync POC. Looks up SKU in Erply, sums stock, updates Voog product stock.")


class SyncConfig:
    def __init__(
        self,
        erply_client_code: str,
        erply_username: str,
        erply_password: str,
        voog_site: str,
        voog_api_token: str,
        erply_api_url: Optional[str] = None,
        erply_warehouse_id: Optional[int] = None,
        sum_all_warehouses: bool = True,
        timeout_seconds: int = 20,
        verbose: bool = False,
    ) -> None:
        self.erply_client_code = erply_client_code
        self.erply_username = erply_username
        self.erply_password = erply_password
        self.voog_site = voog_site
        self.voog_api_token = voog_api_token
        self.erply_api_url = (
            erply_api_url or f"https://{erply_client_code}.erply.com/api/"
        )
        self.erply_warehouse_id = erply_warehouse_id
        self.sum_all_warehouses = sum_all_warehouses
        self.timeout_seconds = timeout_seconds
        self.verbose = verbose


def log(message: str, verbose: bool) -> None:
    if verbose:
        typer.echo(message)


def erply_api_request(
    url: str,
    payload: Dict[str, Any],
    timeout_seconds: int,
    verbose: bool = False,
) -> Dict[str, Any]:
    headers = {"User-Agent": "voog-erply-sync/0.1"}
    log(
        f"POST {url} payload={json.dumps({k: v for k, v in payload.items() if k not in ['username', 'password', 'sessionKey']})}",
        verbose,
    )
    response = requests.post(url, data=payload, headers=headers, timeout=timeout_seconds)
    response.raise_for_status()
    data = response.json()
    status = data.get("status", {})
    error_code = status.get("errorCode")
    if error_code not in (None, 0):
        raise RuntimeError(f"Erply API error: code={error_code} msg={status.get('errorField') or status}")
    return data


def erply_get_session_key(cfg: SyncConfig) -> str:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "verifyUser",
        "username": cfg.erply_username,
        "password": cfg.erply_password,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    # Response usually includes records: [{"sessionKey": "..."}] or top-level sessionKey
    records = data.get("records") or []
    if records and isinstance(records, list):
        first = records[0]
        if isinstance(first, dict) and "sessionKey" in first:
            return first["sessionKey"]
    # Some deployments may return sessionKey at top level
    if "sessionKey" in data:
        return data["sessionKey"]
    raise RuntimeError("Could not obtain Erply sessionKey from response")


def erply_find_product_ids_by_sku(cfg: SyncConfig, session_key: str, sku: str) -> List[int]:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getProducts",
        "sessionKey": session_key,
        # Try typical code fields; Erply supports multiple code fields
        "code": sku,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    records = data.get("records") or []
    product_ids: List[int] = []
    for rec in records:
        product_id = rec.get("productID") or rec.get("id")
        rec_code = rec.get("code") or rec.get("code2") or rec.get("code3")
        if product_id and (rec_code == sku or not rec_code):
            product_ids.append(int(product_id))
    return product_ids


def erply_get_stock_for_products(
    cfg: SyncConfig, session_key: str, product_ids: List[int]
) -> Dict[int, float]:
    if not product_ids:
        return {}
    ids_csv = ",".join(str(pid) for pid in product_ids)
    payload: Dict[str, Any] = {
        "clientCode": cfg.erply_client_code,
        "request": "getProductStock",
        "sessionKey": session_key,
        "productIDs": ids_csv,
    }
    if cfg.erply_warehouse_id and not cfg.sum_all_warehouses:
        payload["warehouseID"] = cfg.erply_warehouse_id
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    records = data.get("records") or []
    stock_per_product: Dict[int, float] = {}
    for rec in records:
        product_id = int(rec.get("productID") or rec.get("id"))
        # Try fields commonly seen: free, amount, total
        amount = rec.get("free")
        if amount is None:
            amount = rec.get("amount")
        if amount is None:
            amount = rec.get("total")
        if amount is None:
            amount = rec.get("amountInStock")
        try:
            amount_float = float(amount or 0)
        except Exception:
            amount_float = 0.0
        if cfg.sum_all_warehouses or cfg.erply_warehouse_id is None:
            stock_per_product[product_id] = stock_per_product.get(product_id, 0.0) + amount_float
        else:
            # When a warehouseID filter is sent, each record should already be that warehouse
            stock_per_product[product_id] = amount_float
    return stock_per_product


def erply_pick_default_warehouse(cfg: SyncConfig, session_key: str) -> int:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getWarehouses",
        "sessionKey": session_key,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    records = data.get("records") or []
    if not records:
        return 1
    for w in records:
        if w.get("active") in (True, 1, "1"):
            wid = w.get("warehouseID") or w.get("id")
            if wid:
                return int(wid)
    wid = records[0].get("warehouseID") or records[0].get("id")
    return int(wid or 1)


def erply_inventory_registration(
    cfg: SyncConfig, session_key: str, product_id: int, amount: float, warehouse_id: Optional[int]
) -> Dict[str, Any]:
    wid = warehouse_id or erply_pick_default_warehouse(cfg, session_key)
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "saveInventoryRegistration",
        "sessionKey": session_key,
        "warehouseID": wid,
        "productID1": product_id,
        "amount1": amount,
    }
    return erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)


def erply_inventory_writeoff(
    cfg: SyncConfig, session_key: str, product_id: int, amount: float, warehouse_id: Optional[int]
) -> Dict[str, Any]:
    wid = warehouse_id or erply_pick_default_warehouse(cfg, session_key)
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "saveInventoryWriteOff",
        "sessionKey": session_key,
        "warehouseID": wid,
        "reasonID": 1,
        "productID1": product_id,
        "amount1": amount,
    }
    return erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)


def voog_get_product_by_sku(voog_site: str, api_token: str, sku: str, timeout_seconds: int, verbose: bool = False) -> Optional[Dict[str, Any]]:
    url = f"https://{voog_site}.voog.com/admin/api/ecommerce/v1/products"
    # Use documented filter syntax
    params = {"q.product.sku.$eq": sku, "per_page": 50}
    headers = {
        "X-API-TOKEN": api_token,
        "Accept": "application/json",
        "User-Agent": "voog-erply-sync/0.1",
    }
    log(f"GET {url} params={params}", verbose)
    resp = requests.get(url, headers=headers, params=params, timeout=timeout_seconds)
    resp.raise_for_status()
    items = resp.json()
    if isinstance(items, list) and items:
        # Expect exactly one item for exact SKU match
        for it in items:
            if it.get("sku") == sku:
                return it
    return None


def voog_update_stock(voog_site: str, api_token: str, product_id: int, stock_value: float, timeout_seconds: int, verbose: bool = False) -> Dict[str, Any]:
    url_bulk = f"https://{voog_site}.voog.com/admin/api/ecommerce/v1/products"
    payload_bulk = {
        "actions": [
            {"target_field": "stock", "action": "set", "value": stock_value}
        ],
        "target_ids": [product_id],
    }

    header_variants = [
        {"X-API-TOKEN": api_token},
    ]

    last_err: Optional[Exception] = None
    for hv in header_variants:
        headers = {
            **hv,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "voog-erply-sync/0.1",
        }
        try:
            log(f"PUT {url_bulk} payload={json.dumps(payload_bulk)} headers_variant={list(hv.keys())}", verbose)
            resp = requests.put(url_bulk, headers=headers, data=json.dumps(payload_bulk), timeout=timeout_seconds)
            if resp.status_code == 401:
                last_err = requests.HTTPError(f"401 Unauthorized (variant {hv})")
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except Exception as e:
            last_err = e
            continue

    # Fallback: try PATCH single-product endpoint
    url_single = f"https://{voog_site}.voog.com/admin/api/ecommerce/v1/products/{product_id}"
    payload_single = {"stock": stock_value}
    for hv in header_variants:
        headers = {
            **hv,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "voog-erply-sync/0.1",
        }
        try:
            log(f"PATCH {url_single} payload={json.dumps(payload_single)} headers_variant={list(hv.keys())}", verbose)
            resp = requests.patch(url_single, headers=headers, data=json.dumps(payload_single), timeout=timeout_seconds)
            if resp.status_code == 401:
                last_err = requests.HTTPError(f"401 Unauthorized (variant {hv})")
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise last_err
    return {}


def load_config(verbose: bool = False, require_voog: bool = True) -> SyncConfig:
    load_dotenv()
    erply_client_code = os.getenv("ERPLY_CLIENT_CODE", "").strip()
    erply_username = os.getenv("ERPLY_USERNAME", "").strip()
    erply_password = os.getenv("ERPLY_PASSWORD", "").strip()
    voog_site = os.getenv("VOOG_SITE", "").strip()
    voog_api_token = os.getenv("VOOG_API_TOKEN", "").strip()
    erply_api_url = os.getenv("ERPLY_API_URL", "").strip() or None
    erply_warehouse_id_env = os.getenv("ERPLY_WAREHOUSE_ID", "").strip()
    erply_warehouse_id = int(erply_warehouse_id_env) if erply_warehouse_id_env else None
    sum_all_warehouses_env = os.getenv("SUM_ALL_WAREHOUSES", "true").strip().lower()
    sum_all_warehouses = sum_all_warehouses_env in ("1", "true", "yes", "y")

    missing: List[str] = []
    if not erply_client_code:
        missing.append("ERPLY_CLIENT_CODE")
    if not erply_username:
        missing.append("ERPLY_USERNAME")
    if not erply_password:
        missing.append("ERPLY_PASSWORD")
    if require_voog:
        if not voog_site:
            missing.append("VOOG_SITE")
        if not voog_api_token:
            missing.append("VOOG_API_TOKEN")
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )

    return SyncConfig(
        erply_client_code=erply_client_code,
        erply_username=erply_username,
        erply_password=erply_password,
        voog_site=voog_site,
        voog_api_token=voog_api_token,
        erply_api_url=erply_api_url,
        erply_warehouse_id=erply_warehouse_id,
        sum_all_warehouses=sum_all_warehouses,
        verbose=verbose,
    )


@app.command()
def sync(
    sku: str = typer.Option(..., help="Product SKU to sync"),
    stock_override: Optional[float] = typer.Option(
        None, help="Override stock value instead of fetching from Erply"
    ),
    warehouse_id: Optional[int] = typer.Option(
        None, help="Erply warehouse ID to read from (overrides ERPLY_WAREHOUSE_ID)"
    ),
    sum_all_warehouses: Optional[bool] = typer.Option(
        None, help="Sum stock across all warehouses (overrides SUM_ALL_WAREHOUSES)"
    ),
    dry_run: bool = typer.Option(False, help="Do not update Voog; just print values"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
):
    """Sync a single SKU: read stock from Erply and update Voog product stock."""
    try:
        cfg = load_config(verbose=verbose)
        if warehouse_id is not None:
            cfg.erply_warehouse_id = warehouse_id
        if sum_all_warehouses is not None:
            cfg.sum_all_warehouses = sum_all_warehouses

        voog_product = voog_get_product_by_sku(
            cfg.voog_site, cfg.voog_api_token, sku, cfg.timeout_seconds, cfg.verbose
        )
        if not voog_product:
            typer.secho(
                f"Voog product not found for SKU {sku}. Create the product with matching SKU first.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)
        voog_product_id = int(voog_product.get("id"))

        if stock_override is not None:
            erply_stock_value = float(stock_override)
        else:
            session_key = erply_get_session_key(cfg)
            product_ids = erply_find_product_ids_by_sku(cfg, session_key, sku)
            if not product_ids:
                typer.secho(
                    f"Erply product not found for SKU {sku}", fg=typer.colors.RED
                )
                raise typer.Exit(code=3)
            stock_map = erply_get_stock_for_products(cfg, session_key, product_ids)
            if not stock_map:
                typer.secho(
                    f"Erply stock response empty for products {product_ids}",
                    fg=typer.colors.YELLOW,
                )
            erply_stock_value = sum(stock_map.values()) if stock_map else 0.0

        typer.echo(
            f"Resolved stock for SKU {sku}: {erply_stock_value} (warehouse_id={cfg.erply_warehouse_id}, sum_all={cfg.sum_all_warehouses})"
        )

        if dry_run:
            typer.echo("Dry-run: skipping Voog update")
            raise typer.Exit(code=0)

        update_resp = voog_update_stock(
            cfg.voog_site,
            cfg.voog_api_token,
            voog_product_id,
            erply_stock_value,
            cfg.timeout_seconds,
            cfg.verbose,
        )
        typer.echo("Voog update response:")
        typer.echo(json.dumps(update_resp, ensure_ascii=False, indent=2))
    except requests.HTTPError as http_err:
        typer.secho(f"HTTP error: {http_err}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def erply_set_stock(
    sku: str = typer.Option(..., help="Product SKU"),
    stock: float = typer.Option(..., help="Target absolute stock quantity"),
    warehouse_id: Optional[int] = typer.Option(None, help="Warehouse ID; default picks active/first"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
):
    """Set absolute stock for SKU in Erply by applying registration/write-off to reach target."""
    try:
        cfg = load_config(verbose=verbose, require_voog=False)
        if warehouse_id is not None:
            cfg.erply_warehouse_id = warehouse_id
        session_key = erply_get_session_key(cfg)
        product_ids = erply_find_product_ids_by_sku(cfg, session_key, sku)
        if not product_ids:
            typer.secho(f"Erply product not found for SKU {sku}", fg=typer.colors.RED)
            raise typer.Exit(code=2)
        product_id = product_ids[0]
        stock_map = erply_get_stock_for_products(cfg, session_key, [product_id])
        current = sum(stock_map.values()) if stock_map else 0.0
        delta = stock - current
        typer.echo(f"Current={current}, target={stock}, delta={delta}")
        if abs(delta) < 1e-9:
            typer.echo("No change needed")
            raise typer.Exit(code=0)
        if delta > 0:
            erply_inventory_registration(cfg, session_key, product_id, delta, cfg.erply_warehouse_id)
        else:
            erply_inventory_writeoff(cfg, session_key, product_id, abs(delta), cfg.erply_warehouse_id)
        typer.echo(json.dumps({"ok": True, "applied_delta": delta}, ensure_ascii=False))
    except Exception as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


# ---- Erply helpers for write operations (create product) ----

def erply_save_product(cfg: SyncConfig, session_key: str, sku: str, name: str) -> Dict[str, Any]:
    # Discover minimal required fields: groupID and vatRateID are often required
    group_id = erply_pick_default_group(cfg, session_key)
    vat_rate_id = erply_pick_default_vat_rate(cfg, session_key)
    payload: Dict[str, Any] = {
        "clientCode": cfg.erply_client_code,
        "request": "saveProduct",
        "sessionKey": session_key,
        "name": name,
        "code": sku,
        "groupID": group_id,
        "vatRateID": vat_rate_id,
        "status": "ACTIVE",
    }
    return erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)


@app.command()
def erply_create_product(
    sku: str = typer.Option(..., help="Product SKU to create in Erply"),
    name: str = typer.Option(..., help="Product name"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
):
    """Create a minimal product in Erply with the given SKU and name."""
    try:
        cfg = load_config(verbose=verbose, require_voog=False)
        session_key = erply_get_session_key(cfg)
        resp = erply_save_product(cfg, session_key, sku=sku, name=name)
        records = resp.get("records") or []
        product_id = None
        if records and isinstance(records, list):
            product_id = records[0].get("productID") or records[0].get("id")
        typer.echo(json.dumps({"ok": True, "product_id": product_id}, ensure_ascii=False))
    except Exception as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


def erply_pick_default_group(cfg: SyncConfig, session_key: str) -> int:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getProductGroups",
        "sessionKey": session_key,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    groups = data.get("records") or []
    if not groups:
        # Fallback to 1 as many accounts have a default group with ID 1
        return 1
    # Prefer an active group; otherwise first
    for g in groups:
        if g.get("active") in (True, 1, "1"):  # active flag may vary
            gid = g.get("productGroupID") or g.get("id") or g.get("groupID")
            if gid:
                return int(gid)
    gid = groups[0].get("productGroupID") or groups[0].get("id") or groups[0].get("groupID")
    return int(gid or 1)


def erply_pick_default_vat_rate(cfg: SyncConfig, session_key: str) -> int:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getVatRates",
        "sessionKey": session_key,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    rates = data.get("records") or []
    if not rates:
        return 1
    # Prefer isDefault or the highest rate named like standard
    for r in rates:
        if r.get("isDefault") in (True, 1, "1"):
            rid = r.get("vatRateID") or r.get("id")
            if rid:
                return int(rid)
    rid = rates[0].get("vatRateID") or rates[0].get("id")
    return int(rid or 1)


# ---- Minimal webhook server for two-way POC ----

class WebhookItem(BaseModel):
    sku: str = Field(..., description="Product SKU")
    quantity: float = Field(..., description="Purchased quantity; positive number")


class OrderWebhook(BaseModel):
    order_id: Optional[str] = None
    items: List[WebhookItem]
    warehouse_id: Optional[int] = None


def create_api() -> FastAPI:
    api = FastAPI(title="Erply↔Voog POC Webhooks")

    @api.get("/healthz")
    async def healthz():
        return {"ok": True}

    @api.post("/voog/order-webhook")
    async def voog_order_webhook(payload: OrderWebhook, request: Request):
        verbose = os.getenv("SYNC_VERBOSE", "false").lower() in ("1", "true", "yes")
        cfg = load_config(verbose=verbose)
        warehouse_override = payload.warehouse_id
        if warehouse_override:
            cfg.erply_warehouse_id = warehouse_override

        # Strategy flags
        write_enabled = os.getenv("ERPLY_WRITE_ENABLED", "false").lower() in ("1", "true", "yes")
        write_strategy = os.getenv("ERPLY_WRITE_STRATEGY", "sync_only").lower()

        # Aggregate items by SKU
        sku_to_qty: Dict[str, float] = {}
        for it in payload.items:
            sku_to_qty[it.sku] = sku_to_qty.get(it.sku, 0.0) + float(it.quantity)

        # Attempt to write to Erply if enabled (not implemented; requires exact API doc choices)
        applied: Dict[str, float] = {}
        if write_enabled and write_strategy != "sync_only":
            # Placeholder: we log intent. Implement after confirming target Erply call (eg saveSalesDocument vs write-off)
            for sku, qty in sku_to_qty.items():
                log(f"[INTENT] Would decrement Erply stock for SKU {sku} by {qty} using strategy={write_strategy}", cfg.verbose)
                applied[sku] = qty

        # In all cases, trigger refresh from Erply to Voog for affected SKUs
        refreshed: Dict[str, float] = {}
        try:
            session_key = erply_get_session_key(cfg)
            for sku in sku_to_qty.keys():
                product_ids = erply_find_product_ids_by_sku(cfg, session_key, sku)
                stock_map = erply_get_stock_for_products(cfg, session_key, product_ids)
                erply_stock_value = sum(stock_map.values()) if stock_map else 0.0
                voog_product = voog_get_product_by_sku(cfg.voog_site, cfg.voog_api_token, sku, cfg.timeout_seconds, cfg.verbose)
                if voog_product:
                    voog_update_stock(cfg.voog_site, cfg.voog_api_token, int(voog_product.get("id")), erply_stock_value, cfg.timeout_seconds, cfg.verbose)
                refreshed[sku] = erply_stock_value
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "order_id": payload.order_id,
            "write_enabled": write_enabled,
            "write_strategy": write_strategy,
            "applied": applied,
            "refreshed": refreshed,
        }

    return api


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8089, help="Bind port"),
):
    """Run webhook listener for two-way POC."""
    uvicorn.run(create_api(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()


