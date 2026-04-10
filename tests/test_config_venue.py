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
