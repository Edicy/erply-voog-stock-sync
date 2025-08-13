import json
import os
from typing import Any, Dict, List, Optional

import requests
import typer
from dotenv import load_dotenv


app = typer.Typer(help="Erply ↔ Voog sync POC v2: stock, price, status two‑way; SKU/name Erply→Voog.")


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
        self.erply_api_url = erply_api_url or f"https://{erply_client_code}.erply.com/api/"
        self.erply_warehouse_id = erply_warehouse_id
        self.sum_all_warehouses = sum_all_warehouses
        self.timeout_seconds = timeout_seconds
        self.verbose = verbose


def log(message: str, verbose: bool) -> None:
    if verbose:
        typer.echo(message)


def load_config(verbose: bool = False) -> SyncConfig:
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
    if not voog_site:
        missing.append("VOOG_SITE")
    if not voog_api_token:
        missing.append("VOOG_API_TOKEN")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

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


# ---- Erply helpers ----

def erply_api_request(url: str, payload: Dict[str, Any], timeout_seconds: int, verbose: bool) -> Dict[str, Any]:
    headers = {"User-Agent": "voog-erply-sync-v2/0.1"}
    safe_payload = {k: v for k, v in payload.items() if k not in {"password", "sessionKey"}}
    log(f"POST {url} payload={json.dumps(safe_payload)}", verbose)
    resp = requests.post(url, data=payload, headers=headers, timeout=timeout_seconds)
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status", {})
    if status.get("errorCode") not in (None, 0):
        raise RuntimeError(f"Erply API error: {status}")
    return data


def erply_get_session_key(cfg: SyncConfig) -> str:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "verifyUser",
        "username": cfg.erply_username,
        "password": cfg.erply_password,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    recs = data.get("records") or []
    if recs and isinstance(recs, list) and isinstance(recs[0], dict):
        key = recs[0].get("sessionKey")
        if key:
            return key
    if "sessionKey" in data:
        return data["sessionKey"]
    raise RuntimeError("No Erply sessionKey")


def erply_find_product_by_sku(cfg: SyncConfig, session_key: str, sku: str) -> Optional[Dict[str, Any]]:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getProducts",
        "sessionKey": session_key,
        "code": sku,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    recs = data.get("records") or []
    return recs[0] if recs else None


def erply_get_stock(cfg: SyncConfig, session_key: str, product_id: int) -> float:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getProductStock",
        "sessionKey": session_key,
        "productIDs": str(product_id),
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    recs = data.get("records") or []
    if not recs:
        return 0.0
    amount = recs[0].get("amountInStock") or recs[0].get("free") or recs[0].get("amount")
    try:
        return float(amount or 0)
    except Exception:
        return 0.0


def erply_set_stock_absolute(cfg: SyncConfig, session_key: str, product_id: int, target: float) -> None:
    current = erply_get_stock(cfg, session_key, product_id)
    delta = target - current
    if abs(delta) < 1e-9:
        return
    # pick default warehouse
    wid = get_default_warehouse(cfg, session_key)
    if delta > 0:
        payload = {
            "clientCode": cfg.erply_client_code,
            "request": "saveInventoryRegistration",
            "sessionKey": session_key,
            "warehouseID": wid,
            "productID1": product_id,
            "amount1": delta,
        }
    else:
        payload = {
            "clientCode": cfg.erply_client_code,
            "request": "saveInventoryWriteOff",
            "sessionKey": session_key,
            "warehouseID": wid,
            "reasonID": 1,
            "productID1": product_id,
            "amount1": abs(delta),
        }
    erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)


def get_default_warehouse(cfg: SyncConfig, session_key: str) -> int:
    payload = {
        "clientCode": cfg.erply_client_code,
        "request": "getWarehouses",
        "sessionKey": session_key,
    }
    data = erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)
    recs = data.get("records") or []
    if not recs:
        return 1
    wid = recs[0].get("warehouseID") or recs[0].get("id")
    return int(wid or 1)


def erply_update_product_fields(cfg: SyncConfig, session_key: str, product_id: int, *, price: Optional[float] = None, status_live: Optional[bool] = None) -> None:
    payload: Dict[str, Any] = {
        "clientCode": cfg.erply_client_code,
        "request": "saveProduct",
        "sessionKey": session_key,
        "productID": product_id,
    }
    if price is not None:
        payload["price"] = price
    if status_live is not None:
        payload["status"] = "ACTIVE" if status_live else "INACTIVE"
    erply_api_request(cfg.erply_api_url, payload, cfg.timeout_seconds, cfg.verbose)


# ---- Voog helpers ----

def voog_get_product_by_sku(voog_site: str, api_token: str, sku: str, timeout_seconds: int, verbose: bool) -> Optional[Dict[str, Any]]:
    url = f"https://{voog_site}.voog.com/admin/api/ecommerce/v1/products"
    params = {"q.product.sku.$eq": sku, "per_page": 50}
    headers = {"X-API-TOKEN": api_token, "Accept": "application/json", "User-Agent": "voog-erply-sync-v2/0.1"}
    log(f"GET {url} params={params}", verbose)
    resp = requests.get(url, headers=headers, params=params, timeout=timeout_seconds)
    resp.raise_for_status()
    items = resp.json()
    if isinstance(items, list):
        for it in items:
            if it.get("sku") == sku:
                return it
    return None


def voog_bulk_update(voog_site: str, api_token: str, product_ids: List[int], actions: List[Dict[str, Any]], timeout_seconds: int, verbose: bool) -> Dict[str, Any]:
    url = f"https://{voog_site}.voog.com/admin/api/ecommerce/v1/products"
    headers = {"X-API-TOKEN": api_token, "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "voog-erply-sync-v2/0.1"}
    payload = {"actions": actions, "target_ids": product_ids}
    log(f"PUT {url} payload={json.dumps(payload)}", verbose)
    resp = requests.put(url, headers=headers, data=json.dumps(payload), timeout=timeout_seconds)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def voog_update_product_fields(voog_site: str, api_token: str, product_id: int, fields: Dict[str, Any], timeout_seconds: int, verbose: bool) -> Dict[str, Any]:
    url = f"https://{voog_site}.voog.com/admin/api/ecommerce/v1/products/{product_id}"
    headers = {"X-API-TOKEN": api_token, "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "voog-erply-sync-v2/0.1"}
    log(f"PUT {url} payload={json.dumps(fields)}", verbose)
    resp = requests.put(url, headers=headers, data=json.dumps(fields), timeout=timeout_seconds)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


# ---- CLI: sync fields ----

@app.command()
def sync_fields(
    sku: str = typer.Option(..., help="Product SKU"),
    direction: str = typer.Option("both", help="erply-to-voog | voog-to-erply | both"),
    include_stock: bool = typer.Option(True, help="Sync stock"),
    include_price: bool = typer.Option(True, help="Sync price"),
    include_status: bool = typer.Option(True, help="Sync status"),
    include_sku_name: bool = typer.Option(True, help="Sync SKU+name Erply→Voog"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    cfg = load_config(verbose=verbose)
    # Resolve resources
    voog_product = voog_get_product_by_sku(cfg.voog_site, cfg.voog_api_token, sku, cfg.timeout_seconds, cfg.verbose)
    if not voog_product:
        typer.secho(f"Voog product with SKU {sku} not found", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    voog_id = int(voog_product["id"]) 

    session_key = erply_get_session_key(cfg)
    erply_product = erply_find_product_by_sku(cfg, session_key, sku)
    if not erply_product:
        typer.secho(f"Erply product with SKU {sku} not found", fg=typer.colors.RED)
        raise typer.Exit(code=3)
    erply_id = int(erply_product.get("productID") or erply_product.get("id"))

    # Gather values
    erply_stock = erply_get_stock(cfg, session_key, erply_id) if include_stock else None
    erply_price = float(erply_product.get("price") or 0) if include_price else None
    erply_status_live = None
    if include_status:
        st = (erply_product.get("status") or erply_product.get("active") or "ACTIVE")
        erply_status_live = True if str(st).upper() in ("ACTIVE", "1", "TRUE") else False
    erply_name = erply_product.get("name") if include_sku_name else None

    voog_stock = int(voog_product.get("stock") or 0) if include_stock else None
    voog_price = float(voog_product.get("price") or 0) if include_price else None
    voog_status_live = (voog_product.get("status") == "live") if include_status else None

    # Apply
    if direction in ("erply-to-voog", "both"):
        actions: List[Dict[str, Any]] = []
        if include_stock and erply_stock is not None:
            actions.append({"target_field": "stock", "action": "set", "value": erply_stock})
        if include_price and erply_price is not None:
            actions.append({"target_field": "price", "action": "set", "value": erply_price})
        if include_status and erply_status_live is not None:
            actions.append({"target_field": "status", "action": "set", "value": "live" if erply_status_live else "draft"})
        if actions:
            voog_bulk_update(cfg.voog_site, cfg.voog_api_token, [voog_id], actions, cfg.timeout_seconds, cfg.verbose)
        if include_sku_name:
            fields: Dict[str, Any] = {}
            if erply_name:
                fields["name"] = erply_name
            # SKU stays same by key; optional write for alignment
            if fields:
                voog_update_product_fields(cfg.voog_site, cfg.voog_api_token, voog_id, fields, cfg.timeout_seconds, cfg.verbose)

    if direction in ("voog-to-erply", "both"):
        if include_stock and voog_stock is not None:
            erply_set_stock_absolute(cfg, session_key, erply_id, float(voog_stock))
        if include_price and voog_price is not None:
            erply_update_product_fields(cfg, session_key, erply_id, price=float(voog_price))
        if include_status and voog_status_live is not None:
            erply_update_product_fields(cfg, session_key, erply_id, status_live=bool(voog_status_live))

    typer.echo("Sync complete")


if __name__ == "__main__":
    app()


