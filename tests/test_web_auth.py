"""Tests for cli.web_auth pairing/signing/transaction relay."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cli import web_auth


def _response(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = status_code < 400
    resp.text = json.dumps(body or {})
    resp.content = resp.text.encode()
    resp.json.return_value = body or {}
    return resp


@pytest.fixture
def pairing_path(tmp_path, monkeypatch):
    path = tmp_path / "pairing.json"
    monkeypatch.setattr(web_auth, "STORAGE_PATH", path)
    monkeypatch.setattr(web_auth, "POLL_INTERVAL_S", 0)
    return path


def test_pairing_roundtrip_preserves_metadata(pairing_path: Path):
    raw = {
        "token": "tok",
        "addresses": ["0x1111111111111111111111111111111111111111"],
        "label": "Wallet",
        "paired_at_ms": 9999999999999,
        "account_id": "acct",
        "master_address": "0x1111111111111111111111111111111111111111",
        "active_session": {"policyId": "p"},
        "agent_wallet_binding": {"walletAddress": "0xagent"},
    }
    pairing_path.write_text(json.dumps(raw), "utf-8")

    result = web_auth.get_stored_pairing()

    assert result is not None
    assert result.account_id == "acct"
    assert result.master_address == "0x1111111111111111111111111111111111111111"
    assert result.active_session == {"policyId": "p"}
    assert result.selected_or_master_address == "0x1111111111111111111111111111111111111111"


def test_select_pairing_address_updates_file(pairing_path: Path):
    pairing_path.write_text(
        json.dumps(
            {
                "token": "tok",
                "addresses": [
                    "0x1111111111111111111111111111111111111111",
                    "0x2222222222222222222222222222222222222222",
                ],
                "label": "Wallet",
                "paired_at_ms": 9999999999999,
            }
        ),
        "utf-8",
    )

    result = web_auth.select_pairing_address("1")

    assert result.selected_address == "0x2222222222222222222222222222222222222222"
    assert json.loads(pairing_path.read_text("utf-8"))["selected_address"] == result.selected_address


def test_sign_with_pair_posts_scope_and_polls_signature(pairing_path: Path, monkeypatch):
    pairing_path.write_text(
        json.dumps(
            {
                "token": "tok",
                "addresses": ["0x1111111111111111111111111111111111111111"],
                "label": "Wallet",
                "paired_at_ms": 9999999999999,
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(web_auth, "_random_request_id", lambda: "req")
    post = MagicMock(return_value=_response(200, {"request_id": "req", "status": "pending"}))
    get = MagicMock(return_value=_response(200, {"status": "signed", "signature": "0x" + "11" * 65}))
    monkeypatch.setattr(web_auth.requests, "post", post)
    monkeypatch.setattr(web_auth.requests, "get", get)

    sig = web_auth.sign_with_pair({"primaryType": "Test"}, "summary", scope={"method": "hl.approveAgent"})

    assert sig == "0x" + "11" * 65
    assert post.call_args.kwargs["json"]["scope"] == {"method": "hl.approveAgent"}
    get.assert_called_once()


def test_submit_transaction_returns_tx_hash(pairing_path: Path, monkeypatch):
    pairing_path.write_text(
        json.dumps(
            {
                "token": "tok",
                "addresses": ["0x1111111111111111111111111111111111111111"],
                "label": "Wallet",
                "paired_at_ms": 9999999999999,
            }
        ),
        "utf-8",
    )
    monkeypatch.setattr(web_auth, "_random_request_id", lambda: "txreq")
    post = MagicMock(return_value=_response(200, {"request_id": "txreq", "status": "pending"}))
    get = MagicMock(return_value=_response(200, {"status": "sent", "tx_hash": "0x" + "aa" * 32}))
    monkeypatch.setattr(web_auth.requests, "post", post)
    monkeypatch.setattr(web_auth.requests, "get", get)

    tx_hash = web_auth.submit_transaction({"from": "0x1111111111111111111111111111111111111111"}, "summary")

    assert tx_hash == "0x" + "aa" * 32
    assert post.call_args.kwargs["json"]["transaction"]["from"].startswith("0x")
