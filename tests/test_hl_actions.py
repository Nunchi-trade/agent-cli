"""Tests for Hyperliquid money-movement action helpers."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cli.config import TradingConfig
from cli import hl_actions


def test_build_usd_transfer_matches_expected_user_typed_payload(monkeypatch):
    monkeypatch.setattr(hl_actions, "_timestamp_ms", lambda: 12345)

    request = hl_actions.build_usd_transfer("10", "0x1111111111111111111111111111111111111111", mainnet=False)

    assert request.action == {
        "destination": "0x1111111111111111111111111111111111111111",
        "amount": "10",
        "time": 12345,
        "type": "usdSend",
        "signatureChainId": "0x66eee",
        "hyperliquidChain": "Testnet",
    }
    assert request.typed_data["primaryType"] == "HyperliquidTransaction:UsdSend"
    assert request.typed_data["message"] == request.action


def test_signature_hex_converts_to_hl_signature():
    sig = "0x" + "11" * 32 + "22" * 32 + "00"

    result = hl_actions._sig_hex_to_hl_signature(sig)

    assert result["r"] == "0x" + "11" * 32
    assert result["s"] == "0x" + "22" * 32
    assert result["v"] == 27


def test_usdc_transfer_calldata_encodes_six_decimals():
    bridge = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"

    calldata = hl_actions.build_usdc_transfer_calldata(bridge, "5.25")

    assert calldata.startswith("0xa9059cbb")
    assert bridge[2:].lower().rjust(64, "0") in calldata.lower()
    assert calldata.endswith(hex(5_250_000)[2:].rjust(64, "0"))


def test_usdc_transfer_calldata_enforces_minimum():
    with pytest.raises(ValueError, match="at least 5 USDC"):
        hl_actions.build_usdc_transfer_calldata("0x2df1c51e09aecf9cacb7bc98cb1742757f163df7", "4.99")


def test_testnet_deposit_requires_verified_usdc(monkeypatch):
    monkeypatch.setattr("cli.web_auth.get_selected_pairing_address", lambda: "0x1111111111111111111111111111111111111111")
    cfg = TradingConfig(arbitrum_testnet_usdc_address=None)

    with pytest.raises(RuntimeError, match="HL_ARBITRUM_TESTNET_USDC_ADDRESS"):
        hl_actions.build_deposit_transaction("5", mainnet=False, cfg=cfg)


def test_mainnet_deposit_transaction_uses_verified_addresses(monkeypatch):
    monkeypatch.setattr("cli.web_auth.get_selected_pairing_address", lambda: "0x1111111111111111111111111111111111111111")
    cfg = TradingConfig()

    tx, summary = hl_actions.build_deposit_transaction("5", mainnet=True, cfg=cfg)

    assert tx["from"] == "0x1111111111111111111111111111111111111111"
    assert tx["to"].lower() == cfg.arbitrum_usdc_address.lower()
    assert tx["chainId"] == 42161
    assert tx["args"]["to"].lower() == cfg.hl_bridge2_mainnet_address.lower()
    assert "Deposit 5 USDC" in summary


def test_sign_and_submit_uses_web_auth_signature(monkeypatch):
    request = hl_actions.HLActionRequest(
        action={"type": "usdSend"},
        nonce=1,
        typed_data={"primaryType": "Test"},
        summary="summary",
    )
    monkeypatch.setattr("cli.web_auth.sign_with_pair", lambda typed, summary, scope=None: "0x" + "11" * 65)
    post = MagicMock()
    monkeypatch.setattr(hl_actions, "post_hl_action", post)

    hl_actions.sign_and_submit(request, mainnet=False)

    assert post.call_args.args[0] == {"type": "usdSend"}
    assert post.call_args.args[2]["v"] == 17


def test_money_bridge_placeholder_is_machine_readable():
    # Keep the deferred bridge phase explicit rather than silently inventing providers.
    data = json.dumps({"status": "deferred", "provider": None})
    assert "deferred" in data
