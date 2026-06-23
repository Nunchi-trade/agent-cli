"""Tests for the minimal web-auth relay client used by money movement."""
from __future__ import annotations

import json
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


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(web_auth, "POLL_INTERVAL_S", 0)


def test_get_stored_pairing_uses_env(monkeypatch):
    monkeypatch.setenv("HL_WEB_AUTH_PAIR_TOKEN", "token")
    monkeypatch.setenv("HL_WEB_AUTH_ADDRESS", "0x1111111111111111111111111111111111111111")

    pairing = web_auth.get_stored_pairing()

    assert pairing is not None
    assert pairing.token == "token"
    assert pairing.selected_or_master_address == "0x1111111111111111111111111111111111111111"


def test_get_stored_pairing_reads_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "pairing.json"
    path.write_text(
        json.dumps(
            {
                "token": "token",
                "addresses": ["0x1111111111111111111111111111111111111111"],
                "selected_address": "0x1111111111111111111111111111111111111111",
            }
        ),
        "utf-8",
    )
    monkeypatch.setenv("HL_WEB_AUTH_PAIRING_PATH", str(path))

    pairing = web_auth.get_stored_pairing()

    assert pairing is not None
    assert pairing.token == "token"


def test_sign_with_pair_posts_scope_and_returns_signature(monkeypatch):
    monkeypatch.setenv("HL_WEB_AUTH_PAIR_TOKEN", "token")
    monkeypatch.setattr(web_auth, "_request_id", lambda: "req")
    post = MagicMock(return_value=_response(200, {"request_id": "req", "status": "pending"}))
    get = MagicMock(return_value=_response(200, {"status": "signed", "signature": "0x" + "11" * 65}))
    monkeypatch.setattr(web_auth.requests, "post", post)
    monkeypatch.setattr(web_auth.requests, "get", get)

    signature = web_auth.sign_with_pair({"primaryType": "Test"}, "summary", scope={"method": "hl.withdraw"})

    assert signature == "0x" + "11" * 65
    assert post.call_args.kwargs["json"]["scope"] == {"method": "hl.withdraw"}


def test_submit_transaction_returns_tx_hash(monkeypatch):
    monkeypatch.setenv("HL_WEB_AUTH_PAIR_TOKEN", "token")
    monkeypatch.setattr(web_auth, "_request_id", lambda: "txreq")
    monkeypatch.setattr(web_auth.requests, "post", MagicMock(return_value=_response(200)))
    monkeypatch.setattr(web_auth.requests, "get", MagicMock(return_value=_response(200, {"status": "sent", "tx_hash": "0x" + "aa" * 32})))

    tx_hash = web_auth.submit_transaction({"from": "0x1111111111111111111111111111111111111111"})

    assert tx_hash == "0x" + "aa" * 32
