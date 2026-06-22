"""Money-movement helpers for Hyperliquid via the existing web-auth relay."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Optional

import requests

from cli.config import TradingConfig


HL_SIGNATURE_CHAIN_ID = "0x66eee"
EIP712_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ERC20_TRANSFER_SELECTOR = "0xa9059cbb"


@dataclass
class SignedActionRequest:
    action: dict[str, Any]
    nonce: int
    typed_data: dict[str, Any]
    summary: str
    scope: Optional[dict[str, Any]] = None


def build_withdraw_request(amount: str, destination: str, mainnet: bool) -> SignedActionRequest:
    nonce = _timestamp_ms()
    action = {
        "destination": _normalize_address(destination),
        "amount": str(_decimal(amount)),
        "time": nonce,
        "type": "withdraw3",
        "signatureChainId": HL_SIGNATURE_CHAIN_ID,
        "hyperliquidChain": "Mainnet" if mainnet else "Testnet",
    }
    typed_data = _user_signed_payload(
        "HyperliquidTransaction:Withdraw",
        [
            {"name": "hyperliquidChain", "type": "string"},
            {"name": "destination", "type": "string"},
            {"name": "amount", "type": "string"},
            {"name": "time", "type": "uint64"},
        ],
        action,
    )
    return SignedActionRequest(
        action=action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Withdraw {amount} USDC from Hyperliquid to {destination}",
        scope={"method": "hl.withdraw", "network": 42161 if mainnet else 421614, "notionalUsdc": float(amount)},
    )


def submit_hl_action(request: SignedActionRequest, mainnet: bool) -> dict[str, Any]:
    from cli.web_auth import sign_with_pair

    signature = _hex_signature_to_hl(sign_with_pair(request.typed_data, request.summary, scope=request.scope))
    payload = {
        "action": request.action,
        "nonce": request.nonce,
        "signature": signature,
        "vaultAddress": None,
        "expiresAfter": None,
    }
    resp = requests.post(f"{_hl_base_url(mainnet)}/exchange", json=payload, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"HL /exchange returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def build_deposit_transaction(amount: str, mainnet: bool, cfg: Optional[TradingConfig] = None) -> tuple[dict[str, Any], str]:
    from cli.web_auth import selected_wallet_address

    cfg = cfg or TradingConfig()
    if mainnet:
        chain_id = cfg.arbitrum_chain_id
        token = cfg.arbitrum_usdc_address
        bridge = cfg.hl_bridge2_mainnet_address
    else:
        chain_id = cfg.arbitrum_testnet_chain_id
        token = cfg.arbitrum_testnet_usdc_address
        bridge = cfg.hl_bridge2_testnet_address

    tx = {
        "from": _normalize_address(selected_wallet_address()),
        "to": _normalize_address(token),
        "data": encode_erc20_transfer(bridge, amount),
        "value": "0x0",
        "chainId": chain_id,
        "contract": _normalize_address(token),
        "method": "transfer",
        "args": {"to": _normalize_address(bridge), "amountUsdc": str(_decimal(amount))},
    }
    return tx, f"Deposit {amount} USDC to Hyperliquid Bridge2"


def encode_erc20_transfer(destination: str, amount: str) -> str:
    value = _decimal(amount)
    if value < Decimal("5"):
        raise ValueError("Hyperliquid Bridge2 deposits require at least 5 USDC.")
    units = int((value * Decimal("1000000")).to_integral_exact(rounding=ROUND_DOWN))
    return ERC20_TRANSFER_SELECTOR + _normalize_address(destination)[2:].rjust(64, "0") + hex(units)[2:].rjust(64, "0")


def fetch_lifi_bridge_quote(
    *,
    from_chain: int,
    from_token: str,
    amount: str,
    from_address: Optional[str] = None,
    to_chain: int = 42161,
    to_token: str = "USDC",
    slippage: Optional[float] = None,
    cfg: Optional[TradingConfig] = None,
) -> dict[str, Any]:
    from cli.web_auth import selected_wallet_address

    cfg = cfg or TradingConfig()
    sender = from_address or selected_wallet_address()
    params: dict[str, Any] = {
        "fromChain": str(from_chain),
        "toChain": str(to_chain),
        "fromToken": from_token,
        "toToken": to_token,
        "fromAmount": _usdc_units(amount),
        "fromAddress": _normalize_address(sender),
        "toAddress": _normalize_address(sender),
    }
    if slippage is not None:
        params["slippage"] = str(slippage)
    resp = requests.get(cfg.lifi_quote_url, params=params, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"LI.FI quote returned {resp.status_code}: {resp.text[:300]}")
    quote = resp.json()
    if not quote.get("transactionRequest"):
        raise RuntimeError("LI.FI quote did not include transactionRequest")
    return quote


def lifi_quote_to_transaction(quote: dict[str, Any], from_address: Optional[str] = None) -> dict[str, Any]:
    tx = dict(quote["transactionRequest"])
    if from_address and not tx.get("from"):
        tx["from"] = _normalize_address(from_address)
    if not tx.get("from"):
        from cli.web_auth import selected_wallet_address

        tx["from"] = _normalize_address(selected_wallet_address())
    if "chainId" not in tx:
        action = quote.get("action") or {}
        from_chain_id = action.get("fromChainId") or action.get("fromChain")
        if from_chain_id is None:
            raise RuntimeError("LI.FI transactionRequest did not include chainId")
        tx["chainId"] = int(from_chain_id)
    tx["value"] = _hex_value(tx.get("value", "0x0"))
    return tx


def _user_signed_payload(primary_type: str, payload_types: list[dict[str, str]], action: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": {
            "name": "HyperliquidSignTransaction",
            "version": "1",
            "chainId": int(HL_SIGNATURE_CHAIN_ID, 16),
            "verifyingContract": EIP712_ZERO_ADDRESS,
        },
        "types": {
            primary_type: payload_types,
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": primary_type,
        "message": action,
    }


def _hex_signature_to_hl(signature: str) -> dict[str, Any]:
    raw = signature[2:] if signature.startswith("0x") else signature
    if len(raw) != 130:
        raise ValueError("Expected a 65-byte EVM signature")
    v = int(raw[128:130], 16)
    if v in (0, 1):
        v += 27
    return {"r": "0x" + raw[:64], "s": "0x" + raw[64:128], "v": v}


def _hl_base_url(mainnet: bool) -> str:
    from hyperliquid.utils import constants

    return constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL


def _timestamp_ms() -> int:
    import time

    return int(time.time() * 1000)


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value}") from exc
    if result <= 0:
        raise ValueError("Amount must be positive.")
    return result


def _usdc_units(amount: str) -> str:
    return str(int((_decimal(amount) * Decimal("1000000")).to_integral_exact(rounding=ROUND_DOWN)))


def _normalize_address(address: str) -> str:
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"Invalid EVM address: {address}")
    int(address[2:], 16)
    return address


def _hex_value(value: Any) -> str:
    if value is None:
        return "0x0"
    if isinstance(value, int):
        return hex(value)
    if isinstance(value, str):
        if value.startswith("0x"):
            return value
        return hex(int(value))
    raise ValueError(f"Invalid transaction value: {value}")
