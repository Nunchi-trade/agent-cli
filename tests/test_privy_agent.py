"""Tests for Privy agent policy helpers."""
from __future__ import annotations

import pytest

from cli.privy_agent import hyperliquid_policy_templates, session_scope, signer_update_payload


def test_policy_templates_include_hl_sensitive_actions():
    templates = hyperliquid_policy_templates(["Testnet"])

    assert templates["deny_withdraw"]["action"] == "DENY"
    assert templates["deny_withdraw"]["conditions"][0]["typed_data"]["primary_type"] == "HyperliquidTransaction:Withdraw"
    assert templates["deny_send_asset"]["conditions"][0]["typed_data"]["primary_type"] == "HyperliquidTransaction:SendAsset"
    assert templates["allow_approve_agent"]["action"] == "ALLOW"
    assert templates["allow_approve_agent"]["conditions"][0]["value"] == ["Testnet"]


def test_signer_update_payload_requires_policy_ids():
    with pytest.raises(ValueError, match="At least one policy"):
        signer_update_payload("signer", [])


def test_signer_update_payload_shape():
    payload = signer_update_payload("signer-1", ["policy-1", "policy-2"])

    assert payload == {
        "additional_signers": [
            {
                "signer_id": "signer-1",
                "override_policy_ids": ["policy-1", "policy-2"],
            }
        ]
    }


def test_session_scope_matches_web_auth_shape():
    scope = session_scope("hl.approveAgent", 42161, notional_usdc=5, instrument_hash="abc")

    assert scope == {
        "method": "hl.approveAgent",
        "network": 42161,
        "notionalUsdc": 5.0,
        "instrumentHash": "abc",
    }
