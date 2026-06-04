"""Tests for cli/session_policy.py — local session-policy enforcement.

Covers: permissive default, action/market/network/strategy/notional violations,
daily-counter accumulation + UTC rollover, and CLI wiring (trade/run) via
CliRunner. No network is touched — trade tests use a stubbed proxy/key.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import typer
from typer.testing import CliRunner

from cli.session_policy import (
    ACTION_BUILDER_APPROVE,
    ACTION_RUN,
    ACTION_TRADE,
    PolicyCounters,
    PolicyViolation,
    SessionPolicy,
    guard_or_exit,
    load_policy,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# SessionPolicy.enforce — unit
# ---------------------------------------------------------------------------

class TestEnforce:
    def test_empty_policy_allows_everything(self):
        pol = SessionPolicy()
        # No constraints set → never raises regardless of context.
        pol.enforce(ACTION_TRADE, wallet="0xabc", network="mainnet",
                    market="DOGE-PERP", strategy="whatever", notional_usd=1e9)

    def test_action_not_allowed(self):
        pol = SessionPolicy(allowed_actions=[ACTION_RUN])
        with pytest.raises(PolicyViolation) as e:
            pol.enforce(ACTION_TRADE)
        assert "action 'trade'" in str(e.value)

    def test_action_allowed(self):
        pol = SessionPolicy(allowed_actions=[ACTION_RUN, ACTION_TRADE])
        pol.enforce(ACTION_TRADE)  # no raise

    def test_market_not_allowed(self):
        pol = SessionPolicy(allowed_markets=["ETH-PERP", "BTC-PERP"])
        with pytest.raises(PolicyViolation) as e:
            pol.enforce(ACTION_TRADE, market="DOGE-PERP")
        assert "market 'DOGE-PERP'" in str(e.value)

    def test_market_allowed(self):
        pol = SessionPolicy(allowed_markets=["ETH-PERP"])
        pol.enforce(ACTION_TRADE, market="ETH-PERP")  # no raise

    def test_network_pin_violation(self):
        pol = SessionPolicy(network="testnet")
        with pytest.raises(PolicyViolation) as e:
            pol.enforce(ACTION_TRADE, network="mainnet")
        assert "network 'mainnet'" in str(e.value)

    def test_network_pin_ok(self):
        pol = SessionPolicy(network="testnet")
        pol.enforce(ACTION_TRADE, network="testnet")

    def test_strategy_not_allowed(self):
        pol = SessionPolicy(allowed_strategies=["avellaneda_mm"])
        with pytest.raises(PolicyViolation) as e:
            pol.enforce(ACTION_RUN, strategy="rogue_strat")
        assert "strategy 'rogue_strat'" in str(e.value)

    def test_wallet_not_allowed_case_insensitive(self):
        pol = SessionPolicy(wallets=["0xAAA111"])
        # different case, still matches
        pol.enforce(ACTION_TRADE, wallet="0xaaa111")
        with pytest.raises(PolicyViolation) as e:
            pol.enforce(ACTION_TRADE, wallet="0xbbb222")
        assert "wallet 0xbbb222" in str(e.value)

    def test_notional_over_limit(self):
        pol = SessionPolicy(max_notional_usd_per_action=5000.0)
        with pytest.raises(PolicyViolation) as e:
            pol.enforce(ACTION_TRADE, notional_usd=5000.01)
        assert "exceeds the per-action limit" in str(e.value)

    def test_notional_at_limit_ok(self):
        pol = SessionPolicy(max_notional_usd_per_action=5000.0)
        pol.enforce(ACTION_TRADE, notional_usd=5000.0)  # equal is allowed

    def test_none_context_skips_checks(self):
        # Constraint set but caller passes None for that dimension → skipped.
        pol = SessionPolicy(allowed_markets=["ETH-PERP"], max_notional_usd_per_action=10.0)
        pol.enforce(ACTION_TRADE, market=None, notional_usd=None)


# ---------------------------------------------------------------------------
# SessionPolicy parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_from_dict_roundtrip(self):
        d = {
            "wallets": ["0xabc"],
            "network": "mainnet",
            "allowed_actions": ["trade"],
            "allowed_strategies": ["s1"],
            "allowed_markets": ["ETH-PERP"],
            "max_notional_usd_per_action": 100.0,
            "daily_notional_limit_usd": 1000.0,
        }
        pol = SessionPolicy.from_dict(d)
        assert pol.to_dict() == d

    def test_unknown_keys_ignored(self):
        pol = SessionPolicy.from_dict({"network": "testnet", "future_field": 123})
        assert pol.network == "testnet"

    def test_bad_network_rejected(self):
        with pytest.raises(ValueError):
            SessionPolicy.from_dict({"network": "regtest"})

    def test_string_for_list_field_rejected(self):
        with pytest.raises(ValueError):
            SessionPolicy.from_dict({"allowed_markets": "ETH-PERP"})

    def test_negative_notional_rejected(self):
        with pytest.raises(ValueError):
            SessionPolicy.from_dict({"max_notional_usd_per_action": -1})

    def test_from_json(self):
        pol = SessionPolicy.from_json('{"network": "testnet"}')
        assert pol.network == "testnet"


# ---------------------------------------------------------------------------
# load_policy — source resolution (file / inline / env / none)
# ---------------------------------------------------------------------------

class TestLoadPolicy:
    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        assert load_policy(None) is None

    def test_explicit_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        f = tmp_path / "policy.json"
        f.write_text(json.dumps({"network": "testnet"}))
        pol = load_policy(str(f))
        assert pol is not None and pol.network == "testnet"

    def test_explicit_inline_json(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        pol = load_policy('{"allowed_markets": ["ETH-PERP"]}')
        assert pol is not None and pol.allowed_markets == ["ETH-PERP"]

    def test_env_file_path(self, tmp_path, monkeypatch):
        f = tmp_path / "p.json"
        f.write_text(json.dumps({"network": "mainnet"}))
        monkeypatch.setenv("NUNCHI_SESSION_POLICY", str(f))
        pol = load_policy(None)
        assert pol is not None and pol.network == "mainnet"

    def test_env_inline_json(self, monkeypatch):
        monkeypatch.setenv("NUNCHI_SESSION_POLICY", '{"network": "testnet"}')
        pol = load_policy(None)
        assert pol is not None and pol.network == "testnet"

    def test_explicit_overrides_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NUNCHI_SESSION_POLICY", '{"network": "mainnet"}')
        pol = load_policy('{"network": "testnet"}')
        assert pol is not None and pol.network == "testnet"

    def test_missing_file_raises(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        with pytest.raises(FileNotFoundError):
            load_policy("/nonexistent/policy/file.json")


# ---------------------------------------------------------------------------
# Daily counters + UTC rollover
# ---------------------------------------------------------------------------

class TestCounters:
    def _counters(self, tmp_path):
        return PolicyCounters(str(tmp_path / "counters.json"))

    def test_record_accumulates(self, tmp_path):
        c = self._counters(tmp_path)
        assert c.record("0xabc", "testnet", "ws1", 100.0) == 100.0
        assert c.record("0xabc", "testnet", "ws1", 50.0) == 150.0
        assert c.used_today("0xabc", "testnet", "ws1") == 150.0

    def test_keys_are_isolated(self, tmp_path):
        c = self._counters(tmp_path)
        c.record("0xabc", "testnet", "ws1", 100.0)
        # different wallet / network / workspace → independent tally
        assert c.used_today("0xdef", "testnet", "ws1") == 0.0
        assert c.used_today("0xabc", "mainnet", "ws1") == 0.0
        assert c.used_today("0xabc", "testnet", "ws2") == 0.0

    def test_utc_rollover_resets(self, tmp_path):
        c = self._counters(tmp_path)
        day1 = datetime(2026, 6, 4, 23, 59, tzinfo=timezone.utc)
        day2 = datetime(2026, 6, 5, 0, 1, tzinfo=timezone.utc)
        c.record("0xabc", "testnet", "ws1", 200.0, now=day1)
        assert c.used_today("0xabc", "testnet", "ws1", now=day1) == 200.0
        # next UTC day → counter reads 0 (rolled over)
        assert c.used_today("0xabc", "testnet", "ws1", now=day2) == 0.0
        # and recording on day2 starts fresh
        assert c.record("0xabc", "testnet", "ws1", 30.0, now=day2) == 30.0

    def test_check_daily_blocks_at_limit(self, tmp_path):
        c = self._counters(tmp_path)
        pol = SessionPolicy(daily_notional_limit_usd=1000.0)
        c.record("0xabc", "testnet", "ws1", 900.0)
        # 900 used + 200 pending > 1000 → blocked
        with pytest.raises(PolicyViolation) as e:
            c.check_daily(pol, "0xabc", "testnet", "ws1", 200.0)
        assert "daily notional limit reached" in str(e.value)

    def test_check_daily_allows_within_limit(self, tmp_path):
        c = self._counters(tmp_path)
        pol = SessionPolicy(daily_notional_limit_usd=1000.0)
        c.record("0xabc", "testnet", "ws1", 900.0)
        c.check_daily(pol, "0xabc", "testnet", "ws1", 100.0)  # exactly at limit, ok

    def test_corrupt_counters_treated_as_empty(self, tmp_path):
        path = tmp_path / "counters.json"
        path.write_text("{ not valid json")
        c = PolicyCounters(str(path))
        assert c.used_today("0xabc", "testnet", "ws1") == 0.0


# ---------------------------------------------------------------------------
# guard_or_exit — the shared CLI helper
# ---------------------------------------------------------------------------

class TestGuardOrExit:
    def test_permissive_when_no_policy(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        assert guard_or_exit(ACTION_TRADE, market="DOGE-PERP", notional_usd=1e9) is None

    def test_returns_policy_when_satisfied(self):
        pol = guard_or_exit(
            ACTION_TRADE,
            policy_path='{"allowed_markets": ["ETH-PERP"]}',
            market="ETH-PERP",
        )
        assert isinstance(pol, SessionPolicy)

    def test_exits_2_on_violation(self):
        with pytest.raises(typer.Exit) as e:
            guard_or_exit(
                ACTION_TRADE,
                policy_path='{"allowed_markets": ["ETH-PERP"]}',
                market="DOGE-PERP",
            )
        assert e.value.exit_code == 2

    def test_exits_2_on_bad_policy(self):
        with pytest.raises(typer.Exit) as e:
            guard_or_exit(ACTION_TRADE, policy_path='{"network": "regtest"}')
        assert e.value.exit_code == 2

    def test_daily_block_via_guard(self, tmp_path):
        cpath = str(tmp_path / "c.json")
        policy_json = '{"daily_notional_limit_usd": 1000.0}'
        # First action: 900 (recorded)
        guard_or_exit(ACTION_TRADE, policy_path=policy_json, network="testnet",
                      wallet="0xabc", notional_usd=900.0, counters_path=cpath,
                      record=True, workspace="ws1")
        # Second action: 200 → would breach 1000 → exit 2
        with pytest.raises(typer.Exit) as e:
            guard_or_exit(ACTION_TRADE, policy_path=policy_json, network="testnet",
                          wallet="0xabc", notional_usd=200.0, counters_path=cpath,
                          record=True, workspace="ws1")
        assert e.value.exit_code == 2


# ---------------------------------------------------------------------------
# CLI wiring — `hl policy`, `hl run --help`, `hl trade --help`
# ---------------------------------------------------------------------------

class TestPolicyCli:
    def _app(self):
        from cli.main import app
        return app

    def test_policy_show_none(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        result = runner.invoke(self._app(), ["policy", "show"])
        assert result.exit_code == 0
        assert "No session policy configured" in result.stdout

    def test_policy_show_inline(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        result = runner.invoke(
            self._app(),
            ["policy", "show", "--policy", '{"network": "testnet"}'],
        )
        assert result.exit_code == 0
        assert '"network": "testnet"' in result.stdout

    def test_policy_validate_ok(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        result = runner.invoke(
            self._app(),
            ["policy", "validate", "--policy", '{"allowed_markets": ["ETH-PERP"]}'],
        )
        assert result.exit_code == 0
        assert "VALID" in result.stdout

    def test_policy_validate_bad(self, monkeypatch):
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)
        result = runner.invoke(
            self._app(),
            ["policy", "validate", "--policy", '{"network": "regtest"}'],
        )
        assert result.exit_code == 2

    def test_run_help_has_policy_option(self):
        result = runner.invoke(self._app(), ["run", "--help"])
        assert result.exit_code == 0
        assert "--policy" in result.stdout

    def test_trade_help_has_policy_option(self):
        result = runner.invoke(self._app(), ["trade", "--help"])
        assert result.exit_code == 0
        assert "--policy" in result.stdout

    def test_builder_approve_help_has_policy_option(self):
        result = runner.invoke(self._app(), ["builder", "approve", "--help"])
        assert result.exit_code == 0
        assert "--policy" in result.stdout


# ---------------------------------------------------------------------------
# CLI wiring — `hl trade` refusal with a stubbed proxy (no network)
# ---------------------------------------------------------------------------

class TestTradeRefusal:
    def _app(self):
        from cli.main import app
        return app

    def test_trade_disallowed_market_refused_pre_connection(self, monkeypatch):
        """A disallowed market is refused before any key/proxy use (exit 2)."""
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)

        # If the guard fails to short-circuit, these would blow up — proving the
        # refusal happens before any network/key access.
        import cli.config as cfgmod

        def _boom(self):
            raise AssertionError("get_private_key must not be reached when market is disallowed")

        monkeypatch.setattr(cfgmod.TradingConfig, "get_private_key", _boom, raising=True)

        result = runner.invoke(
            self._app(),
            ["trade", "DOGE-PERP", "buy", "1", "--price", "10",
             "--policy", '{"allowed_markets": ["ETH-PERP"]}'],
        )
        assert result.exit_code == 2
        assert "REFUSED by session policy" in result.output
        assert "DOGE-PERP" in result.output

    def test_trade_over_notional_refused(self, monkeypatch, tmp_path):
        """Over-limit notional is refused after price resolve, before placing.

        We stub the proxy so no network is hit; the guard should still refuse
        because size*price exceeds max_notional_usd_per_action.
        """
        monkeypatch.delenv("NUNCHI_SESSION_POLICY", raising=False)

        class _StubProxy:
            _address = "0xstub"

            def __init__(self, *a, **k):
                pass

            def get_snapshot(self, instrument):
                raise AssertionError("price given, snapshot must not be called")

            def place_order(self, **k):
                raise AssertionError("must not place order when over limit")

        # trade_cmd does lazy `from parent.hl_proxy import HLProxy` and
        # `from cli.hl_adapter import DirectHLProxy`, so patch them at source.
        import parent.hl_proxy as hlproxy_mod
        import cli.hl_adapter as adapter_mod
        monkeypatch.setattr(hlproxy_mod, "HLProxy", _StubProxy, raising=True)
        monkeypatch.setattr(adapter_mod, "DirectHLProxy", lambda raw: raw, raising=True)

        import cli.config as cfgmod
        monkeypatch.setattr(
            cfgmod.TradingConfig, "get_private_key",
            lambda self: "0x" + "1" * 64, raising=True,
        )

        # size 10 * price 1000 = 10_000 notional > 5_000 limit
        result = runner.invoke(
            self._app(),
            ["trade", "ETH-PERP", "buy", "10", "--price", "1000",
             "--policy", '{"max_notional_usd_per_action": 5000}'],
        )
        assert result.exit_code == 2
        assert "REFUSED by session policy" in result.output
        assert "per-action limit" in result.output
