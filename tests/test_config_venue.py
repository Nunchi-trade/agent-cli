from cli.config import TradingConfig


def test_default_venue_is_hl():
    cfg = TradingConfig()
    assert cfg.venue == "hl"


def test_get_private_key_passes_selected_venue(monkeypatch):
    captured = {}

    def fake_resolve_private_key(venue="hl", address=None):
        captured["venue"] = venue
        return "secret"

    monkeypatch.setattr("common.credentials.resolve_private_key", fake_resolve_private_key)
    cfg = TradingConfig(venue="paradex")
    assert cfg.get_private_key() == "secret"
    assert captured["venue"] == "paradex"


def test_paradex_wallet_address_accepts_l2_length(monkeypatch):
    monkeypatch.setenv("PARADEX_L2_ADDRESS", "0x64ee52a0aefc5317d0d9d34fa3ac620a8f40fa70497a49c7e959be1f1a8f6a")
    from common.credentials import resolve_wallet_address

    assert resolve_wallet_address("paradex") == "0x64ee52a0aefc5317d0d9d34fa3ac620a8f40fa70497a49c7e959be1f1a8f6a"
