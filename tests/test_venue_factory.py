from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from cli.venue_factory import build_venue_adapter, normalize_venue


class _FakeBaseModel:
    pass


def _fake_field(*args, **kwargs):
    return kwargs.get("default", None)


def test_normalize_venue_accepts_aliases():
    assert normalize_venue("hl") == "hl"
    assert normalize_venue("Hyperliquid") == "hl"
    assert normalize_venue("pdx") == "paradex"


def test_normalize_venue_rejects_unknown():
    with pytest.raises(ValueError, match="Unsupported venue"):
        normalize_venue("kraken")


def test_build_venue_adapter_mock_uses_mock_adapter():
    adapter, mode = build_venue_adapter(venue="hl", mock=True)
    assert adapter.__class__.__name__ == "MockVenueAdapter"
    assert mode == "MOCK"


def test_build_venue_adapter_paradex_uses_adapter(monkeypatch):
    class FakeAPIClient:
        def __init__(self):
            self.jwt = "jwt"
        def fetch_markets(self):
            return [{"symbol": "BTC-USD-PERP", "mark_price": "100"}]
        def fetch_balances(self):
            return []
        def fetch_positions(self):
            return []
        def fetch_orders(self):
            return []

    class FakeParadexSubkey:
        def __init__(self, env, l2_private_key, l2_address):
            self.api_client = FakeAPIClient()
            self.ws_client = object()
        def auth(self):
            return {"jwt": "jwt"}

    monkeypatch.setitem(sys.modules, "paradex_py", SimpleNamespace(ParadexSubkey=FakeParadexSubkey, Paradex=FakeParadexSubkey))
    monkeypatch.setitem(sys.modules, "pydantic", SimpleNamespace(BaseModel=_FakeBaseModel, Field=_fake_field))

    monkeypatch.setattr("common.credentials.resolve_private_key", lambda venue="hl", address=None: "0xabc")
    monkeypatch.setattr("common.credentials.resolve_wallet_address", lambda venue="hl", address=None: "0x" + "1" * 40)

    adapter, mode = build_venue_adapter(venue="paradex", mock=False)
    assert adapter.__class__.__name__ == "ParadexVenueAdapter"
    assert mode == "LIVE (testnet)"
