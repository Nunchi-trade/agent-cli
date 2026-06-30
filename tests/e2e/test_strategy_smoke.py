"""Registry-wide strategy smoke tests through the real CLI entrypoint."""
from __future__ import annotations

import os

import pytest

from cli.strategy_registry import STRATEGY_REGISTRY


pytestmark = [pytest.mark.e2e, pytest.mark.slow]

LLM_OR_LIVE_ONLY = {"ai_agent"}


@pytest.mark.parametrize("strategy_name", sorted(STRATEGY_REGISTRY))
def test_registered_strategy_runs_one_mock_tick(run_cli, tmp_path, strategy_name: str):
    if strategy_name in LLM_OR_LIVE_ONLY:
        pytest.skip(f"{strategy_name} requires external model credentials")

    data_dir = tmp_path / strategy_name
    result = run_cli(
        [
            "run",
            strategy_name,
            "--mock",
            "--max-ticks",
            "1",
            "--tick",
            "0",
            "--data-dir",
            str(data_dir),
        ],
        timeout=120,
    )

    assert "Mode: MOCK" in result.stdout
    assert f"Strategy: {strategy_name}" in result.stdout
    assert (data_dir / "state.db").exists()


@pytest.mark.parametrize("strategy_name", sorted(STRATEGY_REGISTRY))
def test_registered_strategy_runs_deeper_mock_loop_and_status(run_cli, tmp_path, strategy_name: str):
    if strategy_name in LLM_OR_LIVE_ONLY:
        pytest.skip(f"{strategy_name} requires external model credentials")

    data_dir = tmp_path / f"{strategy_name}-deep"
    result = run_cli(
        [
            "run",
            strategy_name,
            "--mock",
            "--max-ticks",
            "3",
            "--tick",
            "0",
            "--data-dir",
            str(data_dir),
        ],
        timeout=120,
    )

    assert "Mode: MOCK" in result.stdout
    assert f"Strategy: {strategy_name}" in result.stdout
    assert (data_dir / "state.db").exists()

    status = run_cli(["status", "--data-dir", str(data_dir)])
    assert strategy_name in status.stdout
    assert "ETH-PERP" in status.stdout


@pytest.mark.live
@pytest.mark.llm
def test_ai_agent_openrouter_one_mock_tick_when_enabled(run_cli, tmp_path):
    import os

    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY")):
        pytest.skip("OPENROUTER_API_KEY or AI_API_KEY is required for OpenRouter E2E")

    data_dir = tmp_path / "ai-agent-openrouter"
    result = run_cli(
        [
            "run",
            "ai_agent",
            "--mock",
            "--max-ticks",
            "1",
            "--tick",
            "0",
            "--model",
            os.environ.get("AGENT_CLI_OPENROUTER_MODEL", "openrouter/auto"),
            "--data-dir",
            str(data_dir),
        ],
        env={
            "AI_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
            "AI_API_KEY": os.environ.get("AI_API_KEY", ""),
        },
        timeout=180,
    )

    assert "Mode: MOCK" in result.stdout
    assert "Strategy: ai_agent" in result.stdout
    assert (data_dir / "state.db").exists()


def test_user_supplied_strategy_module_path_runs_as_agent(run_cli, tmp_path, repo_root):
    strategy_file = tmp_path / "custom_agent.py"
    strategy_file.write_text(
        """
from common.models import StrategyDecision
from sdk.strategy_sdk.base import BaseStrategy


class CustomAgent(BaseStrategy):
    def __init__(self, **kwargs):
        super().__init__(strategy_id="custom_agent")

    def on_tick(self, snapshot, context=None):
        return [
            StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy",
                size=0.01,
                limit_price=snapshot.bid,
                order_type="Ioc",
                meta={"signal": "custom_agent_e2e"},
            )
        ]
""".strip(),
        encoding="utf-8",
    )
    data_dir = tmp_path / "custom-agent-data"
    result = run_cli(
        [
            "run",
            "custom_agent:CustomAgent",
            "--mock",
            "--max-ticks",
            "2",
            "--tick",
            "0",
            "--data-dir",
            str(data_dir),
        ],
        env={"PYTHONPATH": os.pathsep.join([str(tmp_path), str(repo_root)])},
    )

    assert "Strategy: custom_agent:CustomAgent" in result.stdout
    assert "Mode: MOCK" in result.stdout
    assert (data_dir / "state.db").exists()
