"""Privy agent policy helpers for Hyperliquid workflows.

These helpers intentionally avoid depending on Privy's Node SDK. They generate
the policy and signer payloads that `web-auth` or an operator can pass to the
Privy SDK/REST API.
"""
from __future__ import annotations

from typing import Any, Optional


HL_MAINNETS = ["Mainnet"]
HL_TESTNETS = ["Testnet"]
HL_BOTH_NETWORKS = ["Testnet", "Mainnet"]


def hyperliquid_policy_templates(networks: Optional[list[str]] = None) -> dict[str, dict[str, Any]]:
    """Return Privy policy bodies for sensitive Hyperliquid user-signed actions."""
    allowed_networks = networks or HL_BOTH_NETWORKS
    return {
        "deny_withdraw": _policy(
            name="Deny Hyperliquid withdrawals",
            action="DENY",
            primary_type="HyperliquidTransaction:Withdraw",
            fields=[
                {"name": "hyperliquidChain", "type": "string"},
                {"name": "destination", "type": "string"},
                {"name": "amount", "type": "string"},
                {"name": "time", "type": "uint64"},
            ],
            networks=allowed_networks,
        ),
        "deny_send_asset": _policy(
            name="Deny Hyperliquid account transfers",
            action="DENY",
            primary_type="HyperliquidTransaction:SendAsset",
            fields=[
                {"name": "hyperliquidChain", "type": "string"},
                {"name": "destination", "type": "string"},
                {"name": "sourceDex", "type": "string"},
                {"name": "destinationDex", "type": "string"},
                {"name": "token", "type": "string"},
                {"name": "amount", "type": "string"},
                {"name": "fromSubAccount", "type": "string"},
                {"name": "nonce", "type": "uint64"},
            ],
            networks=allowed_networks,
        ),
        "allow_approve_agent": _policy(
            name="Allow Hyperliquid approve agent",
            action="ALLOW",
            primary_type="HyperliquidTransaction:ApproveAgent",
            fields=[
                {"name": "hyperliquidChain", "type": "string"},
                {"name": "agentAddress", "type": "address"},
                {"name": "agentName", "type": "string"},
                {"name": "nonce", "type": "uint64"},
            ],
            networks=allowed_networks,
        ),
    }


def signer_update_payload(signer_id: str, policy_ids: list[str]) -> dict[str, Any]:
    """Build the Privy wallet update payload for attaching a policy-scoped signer."""
    if not signer_id:
        raise ValueError("signer_id is required")
    if not policy_ids:
        raise ValueError("At least one policy id is required")
    return {
        "additional_signers": [
            {
                "signer_id": signer_id,
                "override_policy_ids": policy_ids,
            }
        ]
    }


def session_scope(
    method: str,
    network: int,
    *,
    notional_usdc: Optional[float] = None,
    instrument_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Build web-auth scope metadata for Privy/session-signer-gated requests."""
    if not method:
        raise ValueError("method is required")
    scope: dict[str, Any] = {"method": method, "network": int(network)}
    if notional_usdc is not None:
        if notional_usdc < 0:
            raise ValueError("notional_usdc must be non-negative")
        scope["notionalUsdc"] = float(notional_usdc)
    if instrument_hash:
        scope["instrumentHash"] = instrument_hash
    return scope


def _policy(
    *,
    name: str,
    action: str,
    primary_type: str,
    fields: list[dict[str, str]],
    networks: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "method": "eth_signTypedData_v4",
        "action": action,
        "conditions": [
            {
                "field_source": "ethereum_typed_data_message",
                "field": "hyperliquidChain",
                "typed_data": {
                    "types": {
                        "EIP712Domain": [
                            {"name": "name", "type": "string"},
                            {"name": "version", "type": "string"},
                            {"name": "chainId", "type": "uint256"},
                            {"name": "verifyingContract", "type": "address"},
                        ],
                        primary_type: fields,
                    },
                    "primary_type": primary_type,
                },
                "operator": "in",
                "value": networks,
            }
        ],
    }
