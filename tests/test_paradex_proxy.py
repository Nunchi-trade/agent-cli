import sys
from types import SimpleNamespace

import pytest

from parent.paradex_proxy import ParadexProxy


class FakeAPIClient:
    def __init__(self):
        self.jwt = "jwt-from-api"

    def fetch_markets(self):
        return [{"symbol": "BTC-USD-PERP", "tick_size": "0.5"}]

    def fetch_balances(self):
        return [{"asset": "USDC", "available": "100"}]

    def fetch_positions(self):
        return [{"market": "BTC-USD-PERP", "size": "1"}]

    def fetch_orders(self):
        return [{"id": "o1", "status": "open"}]

    def fetch_fills(self):
        return [{"id": "f1", "market": "BTC-USD-PERP", "qty": "0.1"}]

    def submit_order(self, order):
        return {"id": "o2", **order}

    def cancel_order(self, order_id):
        return {"cancelled": order_id}

    def cancel_all_orders(self):
        return {"cancelled": "all"}


class FakeParadexSubkey:
    def __init__(self, env, l2_private_key, l2_address):
        self.env = env
        self.l2_private_key = l2_private_key
        self.l2_address = l2_address
        self.api_client = FakeAPIClient()
        self.ws_client = object()

    def auth(self):
        return {"jwt": "jwt-from-auth"}


class FakeParadex:
    def __init__(self, env, l1_address=None, l1_private_key=None, l2_private_key=None):
        self.env = env
        self.l1_address = l1_address
        self.l1_private_key = l1_private_key
        self.l2_private_key = l2_private_key
        self.api_client = FakeAPIClient()
        self.ws_client = object()

    def auth(self):
        return {"jwt": "jwt-from-auth"}


def install_fake_paradex_sdk(monkeypatch):
    fake_module = SimpleNamespace(ParadexSubkey=FakeParadexSubkey, Paradex=FakeParadex)
    monkeypatch.setitem(sys.modules, "paradex_py", fake_module)


def test_proxy_initializes_and_authenticates(monkeypatch):
    install_fake_paradex_sdk(monkeypatch)
    proxy = ParadexProxy(l2_private_key="0xabc", l2_address="0x" + "1" * 40, testnet=True)

    proxy.connect()

    assert proxy.sdk_env == "testnet"
    assert proxy.rest_url.endswith("testnet.paradex.trade/v1")
    assert proxy.ws_url.endswith("testnet.paradex.trade/v1")
    assert proxy.jwt_token() == "jwt-from-auth"


def test_proxy_fetch_and_account_methods(monkeypatch):
    install_fake_paradex_sdk(monkeypatch)
    proxy = ParadexProxy(l2_private_key="0xabc", l2_address="0x" + "2" * 40, testnet=False)

    assert proxy.sdk_env == "prod"
    assert proxy.fetch_markets()[0]["symbol"] == "BTC-USD-PERP"
    assert proxy.fetch_balances()[0]["asset"] == "USDC"
    assert proxy.fetch_positions()[0]["market"] == "BTC-USD-PERP"
    assert proxy.fetch_orders()[0]["id"] == "o1"
    assert proxy.fetch_fills()[0]["id"] == "f1"

    account = proxy.get_account_state()
    assert account["venue"] == "paradex"
    assert account["network"] == "prod"
    assert account["address"] == "0x" + "2" * 40


def test_proxy_order_methods(monkeypatch):
    install_fake_paradex_sdk(monkeypatch)
    proxy = ParadexProxy(l2_private_key="0xabc", l2_address="0x" + "3" * 40)

    result = proxy.submit_order({"symbol": "BTC-USD-PERP", "side": "BUY"})
    assert result["id"] == "o2"
    assert proxy.placed_orders[-1]["id"] == "o2"
    assert proxy.cancel_order("o2") == {"cancelled": "o2"}
    assert proxy.cancel_all_orders() == {"cancelled": "all"}


def test_proxy_reconcile_private_state(monkeypatch):
    install_fake_paradex_sdk(monkeypatch)
    proxy = ParadexProxy(l2_private_key="0xabc", l2_address="0x" + "4" * 40)

    summary = proxy.reconcile_private_state()
    assert summary["open_orders"] == 1
    assert summary["positions"] == 1
    assert summary["balances"] == 1
    assert summary["needs_rest_snapshot"] is False


def test_proxy_accepts_stark_l2_and_derives_evm_l1_from_private_key(monkeypatch):
    install_fake_paradex_sdk(monkeypatch)
    proxy = ParadexProxy(
        l1_private_key="0x6a894ea7c48501486dba0a91aa6a9f26ab7a7a82df937d670f240145a371d137",
        l2_private_key="0xabc",
        l2_address="0x64ee52a0aefc5317d0d9d34fa3ac620a8f40fa70497a49c7e959be1f1a8f6a",
    )

    assert proxy.l1_address == "0x3b865Cd5Ff31b8aDc667A5Ee655255eB85eb87F3"
    assert proxy.l2_address == "0x64ee52a0aefc5317d0d9d34fa3ac620a8f40fa70497a49c7e959be1f1a8f6a"


def test_proxy_requires_sdk_for_real_use(monkeypatch):
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "paradex_py":
            raise ImportError("missing paradex_py")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.delitem(sys.modules, "paradex_py", raising=False)
    proxy = ParadexProxy(l2_private_key="0xabc", l2_address="0x" + "5" * 40)

    with pytest.raises(RuntimeError, match="paradex_py is not installed"):
        proxy.connect()
