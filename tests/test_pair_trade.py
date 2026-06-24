from __future__ import annotations

import json
from decimal import Decimal

from typer.testing import CliRunner

from cli.main import app
from cli.pear_config import PEAR_BUILDER_ADDRESS, PEAR_BUILDER_FEE_TENTHS_BPS
from strategies.cfi_hedge import BTCSWP_PROFILE
from strategies.pear_pair_trade import build_btc_btcswp_pair_plan


runner = CliRunner()


class FakeFill:
    def __init__(self, oid: str, instrument: str, side: str, quantity: float, price: float):
        self.oid = oid
        self.instrument = instrument
        self.side = side
        self.quantity = Decimal(str(quantity))
        self.price = Decimal(str(price))
        self.timestamp_ms = 123
        self.fee = Decimal("0")


def test_pair_plan_shapes_long_btc_long_btcswp():
    plan = build_btc_btcswp_pair_plan(
        primary_side="long",
        primary_notional_usd=150_000,
        btc_mid=75_000,
        btcswp_mid=75_000,
        hedge_goal="funding_spike",
        builder={"b": "0xBUILDER", "f": 100},
        now_ms=1,
    )

    body = plan.as_dict()
    assert body["eligible"] is True
    assert body["pair_position_id"] == "PAIR-BTC-BTCSWP-1"
    assert body["orders"][0]["instrument"] == "BTC-PERP"
    assert body["orders"][0]["side"] == "buy"
    assert body["orders"][1]["instrument"] == "BTCSWP-USDYP"
    assert body["orders"][1]["side"] == "buy"
    assert body["long_assets"][0]["asset"] == "BTC"
    assert body["builder"] == {"b": "0xBUILDER", "f": 100}


def test_pair_plan_auto_short_btc_sells_btcswp():
    plan = build_btc_btcswp_pair_plan(
        primary_side="short",
        primary_notional_usd=150_000,
        btc_mid=75_000,
        btcswp_mid=75_000,
        hedge_goal="auto",
        now_ms=1,
    )

    body = plan.as_dict()
    assert body["orders"][0]["side"] == "sell"
    assert body["orders"][1]["side"] == "sell"
    assert len(body["short_assets"]) == 2


def test_pair_quote_command_outputs_json():
    result = runner.invoke(
        app,
        [
            "pair", "quote",
            "--primary-side", "long",
            "--primary-notional-usd", "150000",
            "--btc-mid", "75000",
            "--btcswp-mid", "75000",
            "--hedge-goal", "funding_spike",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["eligible"] is True
    assert payload["orders"][0]["instrument"] == "BTC-PERP"
    assert payload["orders"][1]["instrument"] == "BTCSWP-USDYP"


def test_pair_execute_dry_run_does_not_open_hl(monkeypatch):
    import cli.commands.pair as pair_cmd

    monkeypatch.setattr(pair_cmd, "_open_hl", lambda mainnet: (_ for _ in ()).throw(AssertionError("should not open HL")))
    result = runner.invoke(
        app,
        [
            "pair", "execute",
            "--venue", "direct",
            "--primary-side", "long",
            "--primary-notional-usd", "150000",
            "--btc-mid", "75000",
            "--btcswp-mid", "75000",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output


def test_pair_execute_submits_two_legs_and_persists(monkeypatch):
    import cli.commands.pair as pair_cmd

    persisted = {}

    class FakeHL:
        _address = "0x" + "1" * 40

        def __init__(self):
            self.calls = []

        def place_order(self, **kwargs):
            self.calls.append(kwargs)
            idx = len(self.calls)
            return FakeFill(f"oid-{idx}", kwargs["instrument"], kwargs["side"], kwargs["size"], kwargs["price"])

    fake_hl = FakeHL()
    monkeypatch.setattr(pair_cmd, "_open_hl", lambda mainnet: fake_hl)
    monkeypatch.setattr(pair_cmd, "_load_positions", lambda: [])
    monkeypatch.setattr(pair_cmd, "_save_positions", lambda positions: persisted.setdefault("positions", positions))

    result = runner.invoke(
        app,
        [
            "pair", "execute",
            "--venue", "direct",
            "--primary-side", "long",
            "--primary-notional-usd", "150000",
            "--btc-mid", "75000",
            "--btcswp-mid", "75000",
            "--builder-address", "0xBUILDER",
            "--builder-fee-tenths-bps", "100",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [c["instrument"] for c in fake_hl.calls] == ["BTC-PERP", "BTCSWP-USDYP"]
    assert all(c["builder"] == {"b": "0xBUILDER", "f": 100} for c in fake_hl.calls)
    assert persisted["positions"][0]["status"] == "active"
    assert len(persisted["positions"][0]["fills"]) == 2


def test_pair_execute_pear_uses_pear_position_api(monkeypatch):
    import cli.commands.pair as pair_cmd

    persisted = {}

    class FakePear:
        def __init__(self):
            self.calls = []

        def create_position(self, **kwargs):
            self.calls.append(kwargs)
            return {"positionId": "pear-pos-1", "orderId": "pear-order-1"}

    fake_pear = FakePear()
    monkeypatch.setattr(pair_cmd, "_open_hl", lambda mainnet: (_ for _ in ()).throw(AssertionError("should not open HL")))
    monkeypatch.setattr(pair_cmd, "_open_pear", lambda: fake_pear)
    monkeypatch.setattr(pair_cmd, "_load_positions", lambda: [])
    monkeypatch.setattr(pair_cmd, "_save_positions", lambda positions: persisted.setdefault("positions", positions))

    result = runner.invoke(
        app,
        [
            "pair", "execute",
            "--primary-side", "long",
            "--primary-notional-usd", "150000",
            "--btc-mid", "75000",
            "--btcswp-mid", "75000",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert fake_pear.calls[0]["long_assets"][0]["asset"] == "BTC"
    assert fake_pear.calls[0]["long_assets"][1]["asset"] == "BTCSWP"
    asset_notional = sum(asset["notional_usd"] for asset in fake_pear.calls[0]["long_assets"])
    asset_notional += sum(asset["notional_usd"] for asset in fake_pear.calls[0]["short_assets"])
    assert fake_pear.calls[0]["usd_value"] == asset_notional
    assert fake_pear.calls[0]["builder"] == {"b": PEAR_BUILDER_ADDRESS, "f": PEAR_BUILDER_FEE_TENTHS_BPS}
    assert persisted["positions"][0]["venue"] == "pear"
    assert persisted["positions"][0]["pear_position_id"] == "pear-pos-1"
    assert persisted["positions"][0]["builder"] == {"b": PEAR_BUILDER_ADDRESS, "f": PEAR_BUILDER_FEE_TENTHS_BPS}


def test_worked_example_long_btc_long_btcswp_hedges_funding_spike(monkeypatch):
    import cli.commands.pair as pair_cmd

    persisted = {}

    class FakePear:
        def __init__(self):
            self.calls = []

        def create_position(self, **kwargs):
            self.calls.append(kwargs)
            return {"positionId": "pear-long-long", "orderId": "pear-order"}

    fake_pear = FakePear()
    monkeypatch.setattr(pair_cmd, "_open_hl", lambda mainnet: (_ for _ in ()).throw(AssertionError("should not open HL")))
    monkeypatch.setattr(pair_cmd, "_open_pear", lambda: fake_pear)
    monkeypatch.setattr(pair_cmd, "_load_positions", lambda: [])
    monkeypatch.setattr(pair_cmd, "_save_positions", lambda positions: persisted.setdefault("positions", positions))

    result = runner.invoke(
        app,
        [
            "pair", "execute",
            "--primary-side", "long",
            "--primary-notional-usd", "150000",
            "--btc-mid", "75000",
            "--btcswp-mid", "75000",
            "--hedge-goal", "funding_spike",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    pear_order = fake_pear.calls[0]
    assert pear_order["short_assets"] == []
    assert [asset["asset"] for asset in pear_order["long_assets"]] == ["BTC", "BTCSWP"]

    btc_leg, btcswp_leg = pear_order["long_assets"]
    assert btc_leg["role"] == "primary"
    assert btc_leg["notional_usd"] == 150_000.0
    assert btcswp_leg["role"] == "funding_hedge"
    assert btcswp_leg["notional_usd"] == 10_000.0

    # Worked one-hour funding spike:
    # long BTC pays floating funding, long BTCSWP receives the excess over K.
    funding_hr = 0.00002
    fixed_hr = BTCSWP_PROFILE.fixed_leg_initial
    hedge_multiplier = BTCSWP_PROFILE.vol_mult_l

    unhedged_btc_funding_cost = btc_leg["notional_usd"] * funding_hr
    btcswp_excess_receipt = btcswp_leg["notional_usd"] * hedge_multiplier * (funding_hr - fixed_hr)
    hedged_net_cost = unhedged_btc_funding_cost - btcswp_excess_receipt
    fixed_leg_cost = btc_leg["notional_usd"] * fixed_hr

    assert abs(hedged_net_cost - fixed_leg_cost) < 1e-12
    assert btcswp_excess_receipt > 0
    assert hedged_net_cost < unhedged_btc_funding_cost
    assert persisted["positions"][0]["pear_position_id"] == "pear-long-long"


def test_pair_execute_repairs_primary_when_hedge_fails(monkeypatch):
    import cli.commands.pair as pair_cmd

    class FakeHL:
        _address = "0x" + "1" * 40

        def __init__(self):
            self.calls = []

        def place_order(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return FakeFill("primary", kwargs["instrument"], kwargs["side"], kwargs["size"], kwargs["price"])
            if len(self.calls) == 2:
                return None
            return FakeFill("repair", kwargs["instrument"], kwargs["side"], kwargs["size"], kwargs["price"])

    fake_hl = FakeHL()
    monkeypatch.setattr(pair_cmd, "_open_hl", lambda mainnet: fake_hl)

    result = runner.invoke(
        app,
        [
            "pair", "execute",
            "--venue", "direct",
            "--primary-side", "long",
            "--primary-notional-usd", "150000",
            "--btc-mid", "75000",
            "--btcswp-mid", "75000",
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert len(fake_hl.calls) == 3
    assert fake_hl.calls[2]["instrument"] == "BTC-PERP"
    assert fake_hl.calls[2]["side"] == "sell"
    assert fake_hl.calls[2]["reduce_only"] is True


def test_pair_close_dry_run_builds_reverse_orders(monkeypatch):
    import cli.commands.pair as pair_cmd

    monkeypatch.setattr(pair_cmd, "_load_positions", lambda: [{
        "pair_position_id": "PAIR-1",
        "status": "active",
        "fills": [
            {"role": "primary", "instrument": "BTC-PERP", "side": "buy", "quantity": "2", "price": "75000"},
            {"role": "funding_hedge", "instrument": "BTCSWP-USDYP", "side": "buy", "quantity": "0.133333", "price": "75000"},
        ],
    }])
    result = runner.invoke(app, ["pair", "close", "PAIR-1", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.split("\nDRY-RUN", 1)[0])
    assert payload["close_orders"][0]["side"] == "sell"
    assert payload["close_orders"][1]["side"] == "sell"


def test_pair_close_pear_calls_close_position(monkeypatch):
    import cli.commands.pair as pair_cmd

    positions = [{
        "pair_position_id": "PAIR-PEAR",
        "pear_position_id": "pear-pos-1",
        "venue": "pear",
        "status": "active",
        "fills": [],
    }]
    persisted = {}

    class FakePear:
        def close_position(self, position_id, **kwargs):
            assert position_id == "pear-pos-1"
            assert kwargs == {"execution_type": "MARKET"}
            return {"positionId": position_id, "status": "CLOSED"}

    monkeypatch.setattr(pair_cmd, "_open_pear", lambda: FakePear())
    monkeypatch.setattr(pair_cmd, "_load_positions", lambda: positions)
    monkeypatch.setattr(pair_cmd, "_save_positions", lambda saved: persisted.setdefault("positions", saved))

    result = runner.invoke(app, ["pair", "close", "PAIR-PEAR", "--yes"])

    assert result.exit_code == 0, result.output
    assert persisted["positions"][0]["status"] == "closed"
    assert persisted["positions"][0]["pear_close_response"]["status"] == "CLOSED"
