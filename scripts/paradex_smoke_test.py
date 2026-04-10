#!/usr/bin/env python3
"""Standalone Paradex smoke test.

Flow:
1. validate SDK + credentials
2. connect/auth
3. fetch markets
4. fetch balances/positions
5. optionally place a tiny test order
6. fetch open orders
7. optionally cancel the order
8. print JSON summary

Environment variables:
- PARADEX_PRIVATE_KEY or PARADEX_L2_PRIVATE_KEY
- PARADEX_L1_PRIVATE_KEY (optional, useful for onboarding/auth with main wallet)
- PARADEX_ADDRESS or PARADEX_L2_ADDRESS
- PARADEX_TESTNET=true|false (default: true)
- PARADEX_SMOKE_MARKET (optional)
- PARADEX_SMOKE_SIDE (default: buy)
- PARADEX_SMOKE_SIZE (optional)
- PARADEX_SMOKE_PRICE (optional)
- PARADEX_SMOKE_PLACE_ORDER=true|false (default: false)
- PARADEX_SMOKE_CANCEL_ORDER=true|false (default: true)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    result = {
        "ok": False,
        "steps": {},
        "errors": [],
        "warnings": [],
        "config": {},
    }

    testnet = truthy(os.environ.get("PARADEX_TESTNET"), default=True)
    should_place = truthy(os.environ.get("PARADEX_SMOKE_PLACE_ORDER"), default=False)
    should_cancel = truthy(os.environ.get("PARADEX_SMOKE_CANCEL_ORDER"), default=True)

    result["config"] = {
        "testnet": testnet,
        "place_order": should_place,
        "cancel_order": should_cancel,
    }

    try:
        from common.credentials import resolve_private_key
        from parent.paradex_proxy import ParadexProxy
    except Exception as e:
        result["errors"].append(f"import failure: {type(e).__name__}: {e}")
        print(json.dumps(result, indent=2))
        return 1

    try:
        import paradex_py  # type: ignore  # noqa: F401
        result["steps"]["sdk"] = {"available": True}
    except Exception as e:
        result["steps"]["sdk"] = {"available": False}
        result["errors"].append(f"sdk unavailable: {type(e).__name__}: {e}")

    credentials_ok = True
    try:
        private_key = resolve_private_key("paradex")
        l1_private_key = os.environ.get("PARADEX_L1_PRIVATE_KEY", "")
        l1_address = os.environ.get("PARADEX_L1_ADDRESS", "")
        l2_address = os.environ.get("PARADEX_L2_ADDRESS") or os.environ.get("PARADEX_ADDRESS", "")
        proxy = ParadexProxy(
            l1_private_key=l1_private_key or None,
            l1_address=l1_address or None,
            l2_private_key=private_key,
            l2_address=l2_address or None,
            testnet=testnet,
        )
        result["steps"]["credentials"] = {
            "private_key": True,
            "l1_private_key": bool(l1_private_key),
            "l1_address": bool(proxy.l1_address),
            "l1_address_value": proxy.l1_address or "",
            "l2_address": bool(proxy.l2_address),
            "l2_address_value": proxy.l2_address or "",
        }
    except Exception as e:
        credentials_ok = False
        result["errors"].append(f"credentials unavailable: {type(e).__name__}: {e}")

    if result["errors"]:
        print(json.dumps(result, indent=2))
        return 2

    try:
        proxy = ParadexProxy(
            l1_private_key=l1_private_key or None,
            l1_address=l1_address or None,
            l2_private_key=private_key,
            l2_address=l2_address or None,
            testnet=testnet,
        )
        proxy.connect()
        token = proxy.jwt_token()
        result["steps"]["connect"] = {
            "connected": True,
            "jwt_present": bool(token),
            "rest_url": proxy.rest_url,
            "ws_url": proxy.ws_url,
            "sdk_env": proxy.sdk_env,
        }
    except Exception as e:
        result["errors"].append(f"connect/auth failed: {type(e).__name__}: {e}")
        print(json.dumps(result, indent=2))
        return 3

    try:
        markets = proxy.fetch_markets()
        sample_symbols = []
        for m in markets[:10]:
            symbol = m.get("symbol") or m.get("market") or m.get("instrument")
            if symbol:
                sample_symbols.append(symbol)
        result["steps"]["markets"] = {
            "count": len(markets),
            "sample": sample_symbols,
        }
    except Exception as e:
        result["errors"].append(f"fetch_markets failed: {type(e).__name__}: {e}")
        print(json.dumps(result, indent=2))
        return 4

    try:
        balances = proxy.fetch_balances()
        positions = proxy.fetch_positions()
        result["steps"]["account"] = {
            "balances_count": len(balances),
            "positions_count": len(positions),
            "balances_sample": balances[:3],
            "positions_sample": positions[:3],
        }
    except Exception as e:
        result["errors"].append(f"fetch account state failed: {type(e).__name__}: {e}")
        print(json.dumps(result, indent=2))
        return 5

    try:
        reconciled = proxy.reconcile_private_state()
        result["steps"]["reconciliation"] = reconciled
    except Exception as e:
        result["warnings"].append(f"reconcile_private_state failed: {type(e).__name__}: {e}")

    selected_market = os.environ.get("PARADEX_SMOKE_MARKET", "")
    selected_side = os.environ.get("PARADEX_SMOKE_SIDE", "buy")
    selected_size = os.environ.get("PARADEX_SMOKE_SIZE", "")
    selected_price = os.environ.get("PARADEX_SMOKE_PRICE", "")

    if not selected_market and markets:
        for m in markets:
            symbol = m.get("symbol") or m.get("market") or m.get("instrument")
            if symbol:
                selected_market = str(symbol)
                break

    order_result = None
    if should_place:
        if not selected_market:
            result["errors"].append("no market available for order placement")
            print(json.dumps(result, indent=2))
            return 6

        metadata = proxy.get_market_metadata(selected_market)
        default_price = (
            metadata.get("mark_price")
            or metadata.get("mid_price")
            or metadata.get("index_price")
            or metadata.get("last_price")
            or metadata.get("best_ask")
            or metadata.get("best_bid")
            or 0
        )
        default_size = metadata.get("min_order_size") or metadata.get("min_size") or metadata.get("size_increment") or 0.001

        try:
            order = {
                "symbol": selected_market,
                "side": selected_side.upper(),
                "size": float(selected_size or default_size),
                "price": float(selected_price or default_price or 0),
                "time_in_force": "GTC",
                "post_only": True,
                "client_id": f"hermes-smoke-{int(__import__('time').time())}",
            }
            order_result = proxy.submit_order(order)
            result["steps"]["place_order"] = order_result
        except Exception as e:
            result["errors"].append(f"submit_order failed: {type(e).__name__}: {e}")
            print(json.dumps(result, indent=2))
            return 7

        try:
            open_orders = proxy.fetch_orders()
            result["steps"]["open_orders_after_place"] = {
                "count": len(open_orders),
                "sample": open_orders[:5],
            }
        except Exception as e:
            result["warnings"].append(f"fetch_orders after place failed: {type(e).__name__}: {e}")

        if should_cancel and order_result:
            oid = order_result.get("id") or order_result.get("order_id") or order_result.get("client_id")
            if oid:
                try:
                    cancel_result = proxy.cancel_order(str(oid))
                    result["steps"]["cancel_order"] = cancel_result
                except Exception as e:
                    result["errors"].append(f"cancel_order failed: {type(e).__name__}: {e}")
                    print(json.dumps(result, indent=2))
                    return 8
            else:
                result["warnings"].append("order placement succeeded but no order id/client id was returned for cancel")
    else:
        result["warnings"].append("order placement skipped; set PARADEX_SMOKE_PLACE_ORDER=true to execute a live tiny test order")

    result["ok"] = True
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
