"""Tests for cli/readiness.py and the setup status / hl-onboard CLI surface.

Network-dependent checks are exercised with probe_network=False so the tests
are deterministic and offline. Graceful-degradation (na / unknown never crash
and never block readiness) is the key invariant.
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from typer.testing import CliRunner

_root = str(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from cli.readiness import (
    HL_TESTNET_ONBOARD_URL,
    build_readiness_report,
    check_ai_provider_key,
    check_fleet_health,
    check_oracle_freshness,
)
from cli.commands.setup import setup_app
from cli.commands.wallet import wallet_app


runner = CliRunner()


# Every check id the report is expected to contain, in order.
EXPECTED_IDS = [
    "cli_installed",
    "hl_sdk_installed",
    "wallet_configured",
    "hl_onboarding",
    "usdyp_claim",
    "builder_code",
    "ai_provider_key",
    "fleet_health",
    "oracle_freshness",
]

VALID_STATUSES = {"pass", "fail", "action_needed", "na", "unknown"}


@pytest.fixture
def clean_wallet_env(monkeypatch, tmp_path):
    """Force a no-wallet, no-key environment so checks are deterministic.

    Points the keystore dir at an empty tmp path so list_keystores() is empty
    regardless of the developer's real ~/.hl-agent.
    """
    import cli.keystore as ks
    monkeypatch.setattr(ks, "KEYSTORE_DIR", tmp_path / "keystore")
    monkeypatch.setattr(ks, "ENV_FILE", tmp_path / "env")
    for var in ("HL_PRIVATE_KEY", "HL_KEYSTORE_PASSWORD", "ANTHROPIC_API_KEY",
                "AI_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
                "OPENROUTER_API_KEY", "BUILDER_ADDRESS", "BUILDER_FEE_TENTHS_BPS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HL_TESTNET", "true")


# ---------------------------------------------------------------------------
# build_readiness_report — shape
# ---------------------------------------------------------------------------

class TestReportShape:
    def test_top_level_keys(self, clean_wallet_env):
        report = build_readiness_report(workspace="acme", probe_network=False)
        assert set(report.keys()) == {"workspace", "network", "ready", "summary", "checks"}
        assert report["workspace"] == "acme"
        assert report["network"] == "testnet"
        assert isinstance(report["ready"], bool)

    def test_all_expected_check_ids_present_in_order(self, clean_wallet_env):
        report = build_readiness_report(probe_network=False)
        ids = [c["id"] for c in report["checks"]]
        assert ids == EXPECTED_IDS

    def test_each_check_has_id_status_detail(self, clean_wallet_env):
        report = build_readiness_report(probe_network=False)
        for c in report["checks"]:
            assert set(c.keys()) == {"id", "status", "detail"}
            assert c["status"] in VALID_STATUSES
            assert isinstance(c["detail"], str) and c["detail"]

    def test_summary_counts_match_checks(self, clean_wallet_env):
        report = build_readiness_report(probe_network=False)
        s = report["summary"]
        assert s["total"] == len(report["checks"])
        assert sum(s["counts"].values()) == s["total"]
        assert s["blocking"] == len(s["blocking_ids"])


# ---------------------------------------------------------------------------
# Readiness logic
# ---------------------------------------------------------------------------

class TestReadinessLogic:
    def test_no_wallet_is_not_ready(self, clean_wallet_env):
        report = build_readiness_report(probe_network=False)
        assert report["ready"] is False
        ids = {c["id"]: c for c in report["checks"]}
        # No wallet is blocking; missing AI key is not, because deterministic
        # strategies do not need an LLM provider.
        assert ids["wallet_configured"]["status"] == "fail"
        assert ids["ai_provider_key"]["status"] == "na"
        assert "ai_provider_key" not in report["summary"]["blocking_ids"]

    def test_missing_ai_key_does_not_block_non_llm_readiness(self, clean_wallet_env):
        check = check_ai_provider_key()
        assert check["status"] == "na"
        assert "only needed" in check["detail"]

    def test_wallet_lookup_error_fails_closed(self, clean_wallet_env, monkeypatch):
        import cli.keystore as ks

        def raise_permission_error():
            raise PermissionError("Operation not permitted: '/Users/samb/.hl-agent'")

        monkeypatch.setattr(ks, "list_keystores", raise_permission_error)

        report = build_readiness_report(probe_network=False)
        ids = {c["id"]: c for c in report["checks"]}
        assert report["ready"] is False
        assert ids["wallet_configured"]["status"] == "fail"
        assert "Operation not permitted" in ids["wallet_configured"]["detail"]
        assert "wallet_configured" in report["summary"]["blocking_ids"]

    def test_builder_configured_by_default(self, clean_wallet_env):
        # BuilderFeeConfig defaults to the Nunchi wallet, so builder_code passes
        # even with no env override.
        report = build_readiness_report(probe_network=False)
        ids = {c["id"]: c for c in report["checks"]}
        assert ids["builder_code"]["status"] == "pass"

    def test_ready_true_when_no_blocking(self, clean_wallet_env, monkeypatch):
        # Make everything non-blocking: stub each check to pass/na.
        import cli.readiness as r
        passing = [
            (lambda: r._check("cli_installed", "pass", "x"), False),
            (lambda: r._check("hl_sdk_installed", "pass", "x"), False),
            (lambda: r._check("wallet_configured", "pass", "x"), False),
            (lambda **k: r._check("hl_onboarding", "pass", "x"), True),
            (lambda **k: r._check("usdyp_claim", "na", "x"), True),
            (lambda: r._check("builder_code", "pass", "x"), False),
            (lambda: r._check("ai_provider_key", "pass", "x"), False),
            (lambda: r._check("fleet_health", "na", "x"), False),
            (lambda: r._check("oracle_freshness", "na", "x"), False),
        ]
        monkeypatch.setattr(r, "_CHECKS", passing)
        report = build_readiness_report(probe_network=True)
        assert report["ready"] is True
        assert report["summary"]["blocking"] == 0


# ---------------------------------------------------------------------------
# Graceful degradation — na / unknown checks
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_fleet_health_na_no_crash(self):
        c = check_fleet_health()
        assert c["id"] == "fleet_health"
        assert c["status"] == "na"

    def test_oracle_freshness_na_no_crash(self):
        c = check_oracle_freshness()
        assert c["id"] == "oracle_freshness"
        assert c["status"] == "na"

    def test_na_checks_do_not_block_readiness(self, clean_wallet_env):
        report = build_readiness_report(probe_network=False)
        ids = {c["id"]: c for c in report["checks"]}
        assert ids["fleet_health"]["status"] == "na"
        assert ids["oracle_freshness"]["status"] == "na"
        # na ids never appear in blocking_ids
        assert "fleet_health" not in report["summary"]["blocking_ids"]
        assert "oracle_freshness" not in report["summary"]["blocking_ids"]

    def test_no_probe_marks_network_checks_unknown(self, clean_wallet_env):
        report = build_readiness_report(probe_network=False)
        ids = {c["id"]: c for c in report["checks"]}
        assert ids["hl_onboarding"]["status"] == "unknown"
        assert ids["usdyp_claim"]["status"] == "unknown"
        # unknown never blocks
        assert "hl_onboarding" not in report["summary"]["blocking_ids"]

    def test_check_exception_becomes_unknown(self, clean_wallet_env, monkeypatch):
        import cli.readiness as r

        def boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(r, "_CHECKS", [(boom, False)])
        monkeypatch.setattr(r, "_STABLE_IDS", {boom: "boom_check"})
        report = build_readiness_report(probe_network=True)
        assert report["checks"][0]["id"] == "boom_check"
        assert report["checks"][0]["status"] == "unknown"
        assert "kaboom" in report["checks"][0]["detail"]


# ---------------------------------------------------------------------------
# Zero fabrication — onboarding URL is the real one, reused
# ---------------------------------------------------------------------------

class TestNoFabrication:
    def test_onboard_url_is_the_real_one(self):
        assert HL_TESTNET_ONBOARD_URL == "https://app.hyperliquid-testnet.xyz"

    def test_mainnet_onboarding_emits_no_testnet_url(self, clean_wallet_env):
        # On mainnet there is no faucet/web-onboard URL to emit.
        report = build_readiness_report(testnet=False, probe_network=False)
        assert report["network"] == "mainnet"
        ids = {c["id"]: c for c in report["checks"]}
        # usdyp claim is na on mainnet
        assert ids["usdyp_claim"]["status"] == "unknown"  # probe skipped


# ---------------------------------------------------------------------------
# CLI surface — setup status / hl-onboard
# ---------------------------------------------------------------------------

class TestSetupStatusCLI:
    def test_status_json_shape(self, clean_wallet_env):
        result = runner.invoke(
            setup_app, ["status", "--json", "--workspace", "ws1", "--no-probe"]
        )
        # exit code 1 because not ready (no wallet), but JSON must still parse.
        payload = json.loads(result.stdout)
        assert payload["workspace"] == "ws1"
        assert payload["ready"] is False
        assert [c["id"] for c in payload["checks"]] == EXPECTED_IDS

    def test_status_json_exit_code_reflects_ready(self, clean_wallet_env):
        result = runner.invoke(setup_app, ["status", "--json", "--no-probe"])
        assert result.exit_code == 1  # not ready -> nonzero

    def test_status_human_readable_runs(self, clean_wallet_env):
        result = runner.invoke(setup_app, ["status", "--no-probe"])
        assert "Readiness Report" in result.stdout
        assert "READY:" in result.stdout
        # na checks should show up without crashing
        assert "fleet_health" in result.stdout
        assert "oracle_freshness" in result.stdout

    def test_hl_onboard_help_resolves(self):
        result = runner.invoke(setup_app, ["hl-onboard", "--help"])
        assert result.exit_code == 0
        assert "onboard" in result.stdout.lower()

    def test_hl_onboard_json_no_wallet(self, clean_wallet_env):
        result = runner.invoke(setup_app, ["hl-onboard", "--json"])
        payload = json.loads(result.stdout)
        assert payload["network"] == "testnet"
        assert payload["browser_action_url"] == HL_TESTNET_ONBOARD_URL
        assert payload["onboarding"]["status"] in VALID_STATUSES


class TestWalletAutoNextSteps:
    def test_wallet_auto_json_has_next_steps(self, clean_wallet_env, tmp_path, monkeypatch):
        # keystore dir already redirected by clean_wallet_env; ensure home write
        # also lands in tmp via HOME so --save-env doesn't touch the real home.
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(wallet_app, ["auto", "--json", "--save-env"])
        payload = json.loads(result.stdout)
        assert payload["onboarding_required"] is True
        assert payload["onboarding_url"] == HL_TESTNET_ONBOARD_URL
        assert any("connect this wallet" in s.lower() for s in payload["next_steps"])

    def test_wallet_auto_human_prints_next_steps(self, clean_wallet_env, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(wallet_app, ["auto", "--save-env"])
        assert "NEXT STEPS" in result.stdout
        assert HL_TESTNET_ONBOARD_URL in result.stdout
