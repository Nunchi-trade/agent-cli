"""Read-only TreadFi contract status helpers.

This module deliberately avoids network calls. It gives CLI and MCP surfaces a
shared, machine-readable view of what is locally known and what remains blocked
until TreadFi/Eng provides a real endpoint contract.
"""
from __future__ import annotations

from typing import Any

from common.models import asset_to_coin, asset_to_instrument


REQUIRED_CONTRACT_FIELDS = (
    "transport",
    "environments",
    "auth",
    "discovery",
    "market_data",
    "campaign_reporting",
    "execution",
    "operations",
    "fixtures",
)


def spec_status() -> dict[str, Any]:
    """Return the current TreadFi integration contract status."""
    return {
        "status": "blocked",
        "reason": "TreadFi endpoint and MCP tool specs are not present in agent-cli.",
        "required_contract_fields": list(REQUIRED_CONTRACT_FIELDS),
        "missing_contract_fields": list(REQUIRED_CONTRACT_FIELDS),
        "known_local_context": {
            "agent_cli_command": "hl",
            "mcp_command": "hl mcp serve",
            "mcp_server": "cli/mcp_server.py",
            "btcswp_instrument": asset_to_instrument("BTCSWP"),
            "btcswp_coin": asset_to_coin("BTCSWP"),
        },
        "next_step": "Provide the TreadFi contract with fixtures before adding live client or execution code.",
    }


def capabilities() -> dict[str, Any]:
    """Return local placeholder capabilities without claiming live support."""
    return {
        "live_discovery_available": False,
        "status": "contract_missing",
        "read_only_placeholders": [
            {
                "name": "spec_status",
                "description": "Report which TreadFi endpoint contract fields are still missing.",
            },
            {
                "name": "market_params",
                "description": "Return locally known BTCSWP identifiers and mark live params unavailable.",
            },
        ],
        "blocked_until_contract": [
            "list_treadfi_tools",
            "read_treadfi_depth",
            "read_treadfi_leaderboard",
            "place_treadfi_quote",
            "cancel_treadfi_quote",
        ],
    }


def market_params(instrument: str = "BTCSWP-USDYP") -> dict[str, Any]:
    """Return locally known BTCSWP identifiers and missing live fields."""
    requested = instrument.upper()
    local_instrument = asset_to_instrument("BTCSWP")
    local_coin = asset_to_coin("BTCSWP")
    known = requested in {"BTCSWP", local_instrument, local_coin.upper()}
    return {
        "requested": instrument,
        "known_locally": known,
        "instrument": local_instrument,
        "coin": local_coin,
        "source": "common.models HIP-3 instrument registry",
        "live_params_available": False,
        "missing_live_fields": [
            "canonical_treadfi_market_id",
            "oracle_reference_price",
            "depth_bands",
            "tick_size",
            "size_decimals",
            "collateral",
            "campaign_window",
            "attribution_rule",
        ],
    }
