from __future__ import annotations

from parent.paradex_proxy import ParadexProxy


class FakeAPIClient:
    def __init__(self):
        self.last_order = None

    def submit_order(self, order):
        self.last_order = order
        side = getattr(order, "side", None) or getattr(order, "order_side", None)
        return {"id": "abc123", "symbol": order.market, "side": side.value, "price": str(order.limit_price), "size": str(order.size)}


def test_submit_order_builds_sdk_order_object():
    proxy = ParadexProxy(l2_private_key="0x1234", l2_address="0x" + "1" * 62, testnet=False)
    proxy._authenticated = True
    api = FakeAPIClient()
    proxy._client = object()
    proxy._api_client = api

    result = proxy.submit_order(
        {
            "symbol": "SOL-USD-PERP",
            "side": "BUY",
            "size": 0.15,
            "price": 86.12,
            "time_in_force": "GTC",
            "client_id": "hermes-test",
        }
    )

    assert api.last_order is not None
    assert api.last_order.market == "SOL-USD-PERP"
    assert api.last_order.order_side.value == "BUY"
    assert str(api.last_order.size) == "0.15"
    assert str(api.last_order.limit_price) == "86.12"
    assert api.last_order.client_id == "hermes-test"
    assert result["id"] == "abc123"
