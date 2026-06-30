"""Hyperliquid money-movement action builders signed through web-auth."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests

from cli.config import TradingConfig


@dataclass
class HLActionRequest:
    action: dict[str, Any]
    nonce: int
    typed_data: dict[str, Any]
    summary: str
    scope: Optional[dict[str, Any]] = None


def assert_money_sdk_support() -> None:
    """Fail early if the installed SDK lacks the money-movement builders we use."""
    try:
        from hyperliquid.utils import signing
    except ImportError as exc:
        raise RuntimeError("hyperliquid-python-sdk is required for HL money movement") from exc

    required = [
        "USD_SEND_SIGN_TYPES",
        "SPOT_TRANSFER_SIGN_TYPES",
        "WITHDRAW_SIGN_TYPES",
        "USD_CLASS_TRANSFER_SIGN_TYPES",
        "SEND_ASSET_SIGN_TYPES",
        "user_signed_payload",
        "action_hash",
        "construct_phantom_agent",
        "l1_payload",
        "get_timestamp_ms",
    ]
    missing = [name for name in required if not hasattr(signing, name)]
    if missing:
        raise RuntimeError(
            "Installed hyperliquid-python-sdk is missing money-movement helpers: "
            + ", ".join(missing)
            + ". Install hyperliquid-python-sdk==0.20.1."
        )


def _timestamp_ms() -> int:
    assert_money_sdk_support()
    from hyperliquid.utils.signing import get_timestamp_ms

    return int(get_timestamp_ms())


def _hl_base_url(mainnet: bool) -> str:
    from hyperliquid.utils import constants

    return constants.MAINNET_API_URL if mainnet else constants.TESTNET_API_URL


def _user_typed_data(
    action: dict[str, Any],
    payload_types: list[dict[str, str]],
    primary_type: str,
    mainnet: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from hyperliquid.utils.signing import user_signed_payload

    signed_action = dict(action)
    signed_action["signatureChainId"] = "0x66eee"
    signed_action["hyperliquidChain"] = "Mainnet" if mainnet else "Testnet"
    return signed_action, user_signed_payload(primary_type, payload_types, signed_action)


def _l1_typed_data(action: dict[str, Any], nonce: int, mainnet: bool) -> dict[str, Any]:
    from hyperliquid.utils.signing import action_hash, construct_phantom_agent, l1_payload

    hashed = action_hash(action, None, nonce, None)
    return l1_payload(construct_phantom_agent(hashed, mainnet))


def _sig_hex_to_hl_signature(signature: str) -> dict[str, Any]:
    raw = signature[2:] if signature.startswith("0x") else signature
    if len(raw) != 130:
        raise ValueError("expected a 65-byte hex signature")
    v = int(raw[128:130], 16)
    if v in (0, 1):
        v += 27
    return {
        "r": "0x" + raw[0:64],
        "s": "0x" + raw[64:128],
        "v": v,
    }


def post_hl_action(action: dict[str, Any], nonce: int, signature: dict[str, Any], mainnet: bool) -> dict[str, Any]:
    payload = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
        "vaultAddress": None,
        "expiresAfter": None,
    }
    resp = requests.post(f"{_hl_base_url(mainnet)}/exchange", json=payload, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"HL /exchange returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def sign_and_submit(request: HLActionRequest, mainnet: bool) -> dict[str, Any]:
    from cli.web_auth import sign_with_pair

    sig_hex = sign_with_pair(request.typed_data, request.summary, scope=request.scope)
    return post_hl_action(request.action, request.nonce, _sig_hex_to_hl_signature(sig_hex), mainnet)


def build_withdraw(amount: str | float, destination: str, mainnet: bool) -> HLActionRequest:
    from hyperliquid.utils.signing import WITHDRAW_SIGN_TYPES

    nonce = _timestamp_ms()
    action = {"destination": destination, "amount": str(amount), "time": nonce, "type": "withdraw3"}
    signed_action, typed_data = _user_typed_data(
        action,
        WITHDRAW_SIGN_TYPES,
        "HyperliquidTransaction:Withdraw",
        mainnet,
    )
    return HLActionRequest(
        action=signed_action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Withdraw {amount} USDC from Hyperliquid to {destination}",
    )


def build_usd_transfer(amount: str | float, destination: str, mainnet: bool) -> HLActionRequest:
    from hyperliquid.utils.signing import USD_SEND_SIGN_TYPES

    nonce = _timestamp_ms()
    action = {"destination": destination, "amount": str(amount), "time": nonce, "type": "usdSend"}
    signed_action, typed_data = _user_typed_data(
        action,
        USD_SEND_SIGN_TYPES,
        "HyperliquidTransaction:UsdSend",
        mainnet,
    )
    return HLActionRequest(
        action=signed_action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Send {amount} USDC on Hyperliquid to {destination}",
    )


def build_spot_transfer(amount: str | float, destination: str, token: str, mainnet: bool) -> HLActionRequest:
    from hyperliquid.utils.signing import SPOT_TRANSFER_SIGN_TYPES

    nonce = _timestamp_ms()
    action = {
        "destination": destination,
        "amount": str(amount),
        "token": token,
        "time": nonce,
        "type": "spotSend",
    }
    signed_action, typed_data = _user_typed_data(
        action,
        SPOT_TRANSFER_SIGN_TYPES,
        "HyperliquidTransaction:SpotSend",
        mainnet,
    )
    return HLActionRequest(
        action=signed_action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Send {amount} {token} spot on Hyperliquid to {destination}",
    )


def build_usd_class_transfer(amount: str | float, to_perp: bool, mainnet: bool) -> HLActionRequest:
    from hyperliquid.utils.signing import USD_CLASS_TRANSFER_SIGN_TYPES

    nonce = _timestamp_ms()
    action = {"type": "usdClassTransfer", "amount": str(amount), "toPerp": to_perp, "nonce": nonce}
    signed_action, typed_data = _user_typed_data(
        action,
        USD_CLASS_TRANSFER_SIGN_TYPES,
        "HyperliquidTransaction:UsdClassTransfer",
        mainnet,
    )
    direction = "spot to perp" if to_perp else "perp to spot"
    return HLActionRequest(
        action=signed_action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Transfer {amount} USDC {direction} on Hyperliquid",
    )


def build_send_asset(
    amount: str | float,
    destination: str,
    token: str,
    source_dex: str,
    destination_dex: str,
    mainnet: bool,
) -> HLActionRequest:
    from hyperliquid.utils.signing import SEND_ASSET_SIGN_TYPES

    nonce = _timestamp_ms()
    action = {
        "type": "sendAsset",
        "destination": destination,
        "sourceDex": source_dex,
        "destinationDex": destination_dex,
        "token": token,
        "amount": str(amount),
        "fromSubAccount": "",
        "nonce": nonce,
    }
    signed_action, typed_data = _user_typed_data(
        action,
        SEND_ASSET_SIGN_TYPES,
        "HyperliquidTransaction:SendAsset",
        mainnet,
    )
    return HLActionRequest(
        action=signed_action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Send {amount} {token} from {source_dex or 'perp'} to {destination_dex or 'perp'} for {destination}",
    )


def build_vault_transfer(vault_address: str, is_deposit: bool, usd: int, mainnet: bool) -> HLActionRequest:
    nonce = _timestamp_ms()
    action = {"type": "vaultTransfer", "vaultAddress": vault_address, "isDeposit": is_deposit, "usd": usd}
    summary_action = "Deposit into" if is_deposit else "Withdraw from"
    return HLActionRequest(
        action=action,
        nonce=nonce,
        typed_data=_l1_typed_data(action, nonce, mainnet),
        summary=f"{summary_action} Hyperliquid vault {vault_address}: {usd} USDC",
    )


def build_sub_account_transfer(sub_account_user: str, is_deposit: bool, usd: int, mainnet: bool) -> HLActionRequest:
    nonce = _timestamp_ms()
    action = {
        "type": "subAccountTransfer",
        "subAccountUser": sub_account_user,
        "isDeposit": is_deposit,
        "usd": usd,
    }
    summary_action = "Deposit to" if is_deposit else "Withdraw from"
    return HLActionRequest(
        action=action,
        nonce=nonce,
        typed_data=_l1_typed_data(action, nonce, mainnet),
        summary=f"{summary_action} Hyperliquid sub-account {sub_account_user}: {usd} USDC",
    )


def build_sub_account_spot_transfer(
    sub_account_user: str,
    is_deposit: bool,
    token: str,
    amount: str | float,
    mainnet: bool,
) -> HLActionRequest:
    nonce = _timestamp_ms()
    action = {
        "type": "subAccountSpotTransfer",
        "subAccountUser": sub_account_user,
        "isDeposit": is_deposit,
        "token": token,
        "amount": str(amount),
    }
    summary_action = "Deposit to" if is_deposit else "Withdraw from"
    return HLActionRequest(
        action=action,
        nonce=nonce,
        typed_data=_l1_typed_data(action, nonce, mainnet),
        summary=f"{summary_action} Hyperliquid sub-account {sub_account_user}: {amount} {token}",
    )


def build_approve_agent(agent_address: str, agent_name: str, mainnet: bool) -> HLActionRequest:
    nonce = _timestamp_ms()
    action = {
        "type": "approveAgent",
        "agentAddress": agent_address,
        "agentName": agent_name,
        "nonce": nonce,
    }
    payload_types = [
        {"name": "hyperliquidChain", "type": "string"},
        {"name": "agentAddress", "type": "address"},
        {"name": "agentName", "type": "string"},
        {"name": "nonce", "type": "uint64"},
    ]
    signed_action, typed_data = _user_typed_data(
        action,
        payload_types,
        "HyperliquidTransaction:ApproveAgent",
        mainnet,
    )
    return HLActionRequest(
        action=signed_action,
        nonce=nonce,
        typed_data=typed_data,
        summary=f"Approve Hyperliquid agent {agent_address} ({agent_name})",
        scope={"method": "hl.approveAgent", "network": 42161 if mainnet else 421614},
    )


def parse_whole_usd(amount: str | float) -> int:
    value = _decimal(amount)
    if value != value.to_integral_value():
        raise ValueError("This Hyperliquid transfer type only accepts whole-USDC amounts.")
    if value <= 0:
        raise ValueError("Amount must be positive.")
    return int(value)


def build_usdc_transfer_calldata(destination: str, amount: str | float) -> str:
    value = _decimal(amount)
    if value < Decimal("5"):
        raise ValueError("Hyperliquid Bridge2 deposits require at least 5 USDC.")
    units = int((value * Decimal("1000000")).to_integral_exact())
    address_arg = _normalize_address(destination)[2:].rjust(64, "0")
    amount_arg = hex(units)[2:].rjust(64, "0")
    return "0xa9059cbb" + address_arg + amount_arg


def build_deposit_transaction(amount: str | float, mainnet: bool, cfg: TradingConfig) -> tuple[dict[str, Any], str]:
    from cli.web_auth import get_selected_pairing_address

    sender = get_selected_pairing_address()
    if mainnet:
        chain_id = cfg.arbitrum_chain_id
        usdc = cfg.arbitrum_usdc_address
        bridge = cfg.hl_bridge2_mainnet_address
    else:
        if not cfg.arbitrum_testnet_usdc_address:
            raise RuntimeError(
                "Set HL_ARBITRUM_TESTNET_USDC_ADDRESS before testnet deposits. "
                "No verified Arbitrum testnet USDC address is hardcoded."
            )
        chain_id = cfg.arbitrum_testnet_chain_id
        usdc = cfg.arbitrum_testnet_usdc_address
        bridge = cfg.hl_bridge2_testnet_address

    calldata = build_usdc_transfer_calldata(bridge, amount)
    tx = {
        "from": _normalize_address(sender),
        "to": _normalize_address(usdc),
        "data": calldata,
        "value": "0x0",
        "chainId": chain_id,
        "contract": _normalize_address(usdc),
        "method": "transfer",
        "args": {"to": _normalize_address(bridge), "amountUsdc": str(amount)},
    }
    return tx, f"Deposit {amount} USDC from Arbitrum to Hyperliquid Bridge2"


def _decimal(value: str | float) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value}") from exc


def _normalize_address(address: str) -> str:
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"Invalid EVM address: {address}")
    int(address[2:], 16)
    return address
