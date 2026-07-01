"""Tests for common.models instrument registry."""
from common.models import (
    active_hip3_dex_ids,
    asset_to_coin,
    asset_to_instrument,
    coin_to_instrument,
    dex_for_instrument,
    get_hip3_dex_ids,
    instrument_to_asset,
    instrument_to_coin,
    is_mainnet,
    normalize_hl_coin,
)


def test_instrument_to_coin_yex():
    assert instrument_to_coin("VXX-USDYP") == "yex:VXX"
    assert instrument_to_coin("BTCSWP-USDYP", mainnet=False) == "yex:BTCSWP"


def test_instrument_to_coin_para_mainnet():
    assert instrument_to_coin("BTCSWP-PARA", mainnet=True) == "para:BTCSWP"
    assert instrument_to_coin("BTCSWP-USDYP", mainnet=True) == "para:BTCSWP"
    assert normalize_hl_coin("para:btcswp") == "para:BTCSWP"


def test_asset_to_instrument_network():
    assert asset_to_instrument("BTCSWP", mainnet=False) == "BTCSWP-USDYP"
    assert asset_to_instrument("BTCSWP", mainnet=True) == "BTCSWP-PARA"


def test_active_hip3_dex_ids():
    assert active_hip3_dex_ids(mainnet=False) == ["yex"]
    assert active_hip3_dex_ids(mainnet=True) == ["para"]


def test_dex_for_instrument_btcswp():
    assert dex_for_instrument("BTCSWP-USDYP", mainnet=True) == "para"
    assert dex_for_instrument("BTCSWP-USDYP", mainnet=False) == "yex"


def test_asset_to_coin():
    assert asset_to_coin("BTCSWP", mainnet=False) == "yex:BTCSWP"
    assert asset_to_coin("BTCSWP", mainnet=True) == "para:BTCSWP"


def test_coin_roundtrip():
    for inst, mainnet in [("BTCSWP-USDYP", False), ("BTCSWP-PARA", True)]:
        assert coin_to_instrument(instrument_to_coin(inst, mainnet=mainnet), mainnet=mainnet) == inst


def test_is_mainnet_from_env(monkeypatch):
    monkeypatch.setenv("HL_TESTNET", "false")
    assert is_mainnet() is True


def test_instrument_to_asset():
    assert instrument_to_asset("BTCSWP-PARA") == "BTCSWP"
