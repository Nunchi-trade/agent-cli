"""Tests for Hyperliquid money-movement helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cli.config import TradingConfig
from cli import hl_money


def test_withdraw_request_builds_hl_typed_data(monkeypatch):
    monkeypatch.setattr(hl_money, "_timestamp_ms", lambda: 123)

    request = hl_money.build_withdraw_request("10", "0x1111111111111111111111111111111111111111", mainnet=False)

    assert request.action["type"] == "withdraw3"
    assert request.action["hyperliquidChain"] == "Testnet"
    assert request.typed_data["primaryType"] == "HyperliquidTransaction:Withdraw"
    assert request.typed_data["message"] == request.action
    assert request.scope == {"method": "hl.withdraw", "network": 421614, "notionalUsdc": 10.0}


def test_encode_erc20_transfer_uses_usdc_decimals():
    calldata = hl_money.encode_erc20_transfer("0x2df1c51e09aecf9cacb7bc98cb1742757f163df7", "5.25")

    assert calldata.startswith("0xa9059cbb")
    assert calldata.endswith(hex(5_250_000)[2:].rjust(64, "0"))


def test_encode_erc20_transfer_enforces_bridge_minimum():
    with pytest.raises(ValueError, match="at least 5 USDC"):
        hl_money.encode_erc20_transfer("0x2df1c51e09aecf9cacb7bc98cb1742757f163df7", "4.99")


def test_build_deposit_transaction_uses_testnet_bridge_and_usdc2(monkeypatch):
    monkeypatch.setattr("cli.web_auth.selected_wallet_address", lambda: "0x1111111111111111111111111111111111111111")
    cfg = TradingConfig()

    tx, summary = hl_money.build_deposit_transaction("5", mainnet=False, cfg=cfg)

    assert tx["from"] == "0x1111111111111111111111111111111111111111"
    assert tx["to"].lower() == cfg.arbitrum_testnet_usdc_address.lower()
    assert tx["chainId"] == 421614
    assert tx["args"]["to"].lower() == cfg.hl_bridge2_testnet_address.lower()
    assert "Deposit 5 USDC" in summary


def test_lifi_quote_to_transaction_fills_missing_from_and_value(monkeypatch):
    monkeypatch.setattr("cli.web_auth.selected_wallet_address", lambda: "0x1111111111111111111111111111111111111111")
    quote = {
        "action": {"fromChainId": 8453},
        "transactionRequest": {
            "to": "0x2222222222222222222222222222222222222222",
            "data": "0x1234",
            "value": "0",
        },
    }

    tx = hl_money.lifi_quote_to_transaction(quote)

    assert tx["from"] == "0x1111111111111111111111111111111111111111"
    assert tx["chainId"] == 8453
    assert tx["value"] == "0x0"


def test_fetch_lifi_bridge_quote_requests_smallest_units(monkeypatch):
    monkeypatch.setattr("cli.web_auth.selected_wallet_address", lambda: "0x1111111111111111111111111111111111111111")
    response = MagicMock()
    response.ok = True
    response.json.return_value = {"transactionRequest": {"to": "0x2222222222222222222222222222222222222222"}}
    get = MagicMock(return_value=response)
    monkeypatch.setattr(hl_money.requests, "get", get)

    quote = hl_money.fetch_lifi_bridge_quote(from_chain=8453, from_token="USDC", amount="12.5")

    assert quote["transactionRequest"]["to"].startswith("0x")
    params = get.call_args.kwargs["params"]
    assert params["fromAmount"] == "12500000"
    assert params["toChain"] == "42161"
