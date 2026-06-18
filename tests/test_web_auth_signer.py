from __future__ import annotations

import pytest


def test_pairing_from_env_prefers_web_auth_address(monkeypatch):
    from cli.web_auth import pairing_from_env

    monkeypatch.setenv("NUNCHI_WEB_AUTH_PAIR_TOKEN", "pair-token")
    monkeypatch.setenv("NUNCHI_WEB_AUTH_ADDRESS", "0x" + "1" * 40)
    monkeypatch.setenv("HL_WALLET_ADDRESS", "0x" + "2" * 40)

    pairing = pairing_from_env()

    assert pairing is not None
    assert pairing.token == "pair-token"
    assert pairing.address == "0x" + "1" * 40


def test_split_signature_normalizes_v():
    from cli.web_auth import split_signature

    sig = "0x" + ("11" * 32) + ("22" * 32) + "01"

    parsed = split_signature(sig)

    assert parsed["r"] == "0x" + "11" * 32
    assert parsed["s"] == "0x" + "22" * 32
    assert parsed["v"] == 28


def test_web_auth_wallet_signs_with_pair(monkeypatch):
    import cli.web_auth as web_auth

    monkeypatch.setattr(
        web_auth,
        "sign_typed_data_with_pair",
        lambda typed_data, token, summary="", timeout_s=0, on_awaiting=None:
            "0x" + ("aa" * 32) + ("bb" * 32) + "1b",
    )

    wallet = web_auth.WebAuthWallet(web_auth.WebAuthPairing(token="tok", address="0x" + "3" * 40))

    sig = wallet.sign_typed_data({"domain": {}, "types": {}, "message": {}})

    assert sig["r"] == "0x" + "aa" * 32
    assert sig["s"] == "0x" + "bb" * 32
    assert sig["v"] == 27


def test_hyperliquid_signer_patch_uses_web_auth_wallet(monkeypatch):
    from cli.web_auth import WebAuthPairing, WebAuthWallet, install_hyperliquid_web_auth_signer
    import hyperliquid.utils.signing as signing

    wallet = WebAuthWallet(WebAuthPairing(token="tok", address="0x" + "4" * 40))
    monkeypatch.setattr(wallet, "sign_typed_data", lambda data: {"r": "0x1", "s": "0x2", "v": 27})

    install_hyperliquid_web_auth_signer()

    assert signing.sign_inner(wallet, {"message": {"hello": "world"}}) == {"r": "0x1", "s": "0x2", "v": 27}


def test_hl_proxy_uses_web_auth_wallet_without_private_key(monkeypatch):
    from parent.hl_proxy import HLProxy

    created = {}

    class FakeInfo:
        def __init__(self, *args, **kwargs):
            pass

    class FakeExchange:
        def __init__(self, wallet, base_url, account_address=None, perp_dexs=None):
            created["wallet"] = wallet
            created["account_address"] = account_address

        def agent_enable_dex_abstraction(self):
            return None

    monkeypatch.setenv("NUNCHI_WEB_AUTH_PAIR_TOKEN", "pair-token")
    monkeypatch.setenv("NUNCHI_WEB_AUTH_ADDRESS", "0x" + "5" * 40)
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    monkeypatch.setattr("hyperliquid.info.Info", FakeInfo)
    monkeypatch.setattr("hyperliquid.exchange.Exchange", FakeExchange)

    proxy = HLProxy(private_key="", testnet=True)
    proxy._ensure_client()

    assert created["wallet"].address == "0x" + "5" * 40
    assert proxy._address == "0x" + "5" * 40


def test_hl_proxy_without_key_or_pairing_still_fails_clearly(monkeypatch):
    from parent.hl_proxy import HLProxy

    monkeypatch.delenv("NUNCHI_WEB_AUTH_PAIR_TOKEN", raising=False)
    monkeypatch.delenv("NUNCHI_WEB_AUTH_ADDRESS", raising=False)

    proxy = HLProxy(private_key="", testnet=True)

    with pytest.raises(Exception):
        proxy._ensure_client()


def test_trading_config_uses_web_auth_when_no_private_key(monkeypatch):
    from cli.config import TradingConfig

    import common.credentials as credentials

    monkeypatch.setattr(
        credentials,
        "resolve_private_key",
        lambda venue="hl": (_ for _ in ()).throw(RuntimeError("missing key")),
    )
    monkeypatch.setenv("NUNCHI_WEB_AUTH_PAIR_TOKEN", "pair-token")
    monkeypatch.setenv("NUNCHI_WEB_AUTH_ADDRESS", "0x" + "6" * 40)

    cfg = TradingConfig()

    assert cfg.get_private_key() == ""
    assert cfg.get_wallet_address() == "0x" + "6" * 40
