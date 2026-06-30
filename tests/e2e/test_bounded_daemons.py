"""Bounded startup checks for commands that are normally long-running."""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def test_guard_start_can_boot_until_timeout_in_mock_mode(run_cli_until_timeout, tmp_path):
    result = run_cli_until_timeout(
        [
            "guard",
            "start",
            "ETH-PERP",
            "--entry",
            "2500",
            "--size",
            "1",
            "--direction",
            "long",
            "--tick",
            "0.25",
            "--mock",
            "--data-dir",
            str(tmp_path / "guard"),
        ],
        timeout=3,
    )

    assert result.returncode == -1
    assert "Mode: MOCK" in result.stdout
    assert "Instrument: ETH-PERP" in result.stdout


def test_radar_run_is_boundable_with_max_scans(run_cli, tmp_path):
    result = run_cli(
        [
            "radar",
            "run",
            "--mock",
            "--max-scans",
            "1",
            "--tick",
            "0",
            "--data-dir",
            str(tmp_path / "radar"),
        ],
        timeout=90,
    )

    assert "Mode: MOCK" in result.stdout
    assert "SCAN #1" in result.stdout


def test_pulse_run_is_boundable_with_max_scans(run_cli, tmp_path):
    result = run_cli(
        [
            "pulse",
            "run",
            "--mock",
            "--max-scans",
            "1",
            "--tick",
            "0",
            "--data-dir",
            str(tmp_path / "pulse"),
        ],
        timeout=90,
    )

    assert "Mode: MOCK" in result.stdout
    assert "PULSE #1" in result.stdout


def test_mcp_serve_fails_cleanly_without_optional_extra_or_can_show_startup(run_cli_bounded, run_cli):
    help_result = run_cli(["mcp", "serve", "--help"])
    assert "transport" in help_result.stdout

    result = run_cli_bounded(["mcp", "serve"], timeout=5)
    if result.returncode == -1:
        assert "Starting MCP server" in result.combined_output
        return
    if result.returncode == 0:
        assert "Starting MCP server" in result.combined_output
        return
    assert (
        "MCP package not installed" in result.combined_output
        or "Starting MCP server" in result.combined_output
        or "No module named" in result.combined_output
    )


def test_telegram_start_fails_fast_without_token(run_cli):
    result = run_cli(["telegram", "start", "--dry-run"], check=False, timeout=10)

    assert result.returncode == 1
    assert "TELEGRAM_BOT_TOKEN not set" in result.combined_output
