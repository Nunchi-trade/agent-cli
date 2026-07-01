"""Shared fixtures for process-boundary agent CLI E2E tests."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import pytest


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass(frozen=True)
class CliResult:
    """Small assertion-friendly wrapper around a subprocess result."""

    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_env(tmp_path: Path, repo_root: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "HL_TESTNET": "true",
            "PYTHONPATH": str(repo_root),
        }
    )
    env.pop("HL_PRIVATE_KEY", None)
    env.pop("HL_KEYSTORE_PASSWORD", None)
    env.pop("API_AUTH_TOKEN", None)
    return env


@pytest.fixture
def e2e_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def run_cli(repo_root: Path, isolated_env: dict[str, str]):
    def _run_cli(
        args: Sequence[str],
        *,
        check: bool = True,
        timeout: float = 120,
        env: Mapping[str, str] | None = None,
    ) -> CliResult:
        merged_env = isolated_env.copy()
        if env:
            merged_env.update(env)

        command = [sys.executable, "-m", "cli.main", *args]
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        result = CliResult(
            args=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            pytest.fail(
                "CLI command failed\n"
                f"command: {' '.join(command)}\n"
                f"exit: {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return result

    return _run_cli


@pytest.fixture
def run_cli_until_timeout(repo_root: Path, isolated_env: dict[str, str]):
    def _run_cli_until_timeout(
        args: Sequence[str],
        *,
        timeout: float = 3,
        env: Mapping[str, str] | None = None,
    ) -> CliResult:
        merged_env = isolated_env.copy()
        if env:
            merged_env.update(env)

        command = [sys.executable, "-m", "cli.main", *args]
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                env=merged_env,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return CliResult(
                args=command,
                returncode=-1,
                stdout=_to_text(exc.stdout),
                stderr=_to_text(exc.stderr),
            )

        pytest.fail(
            "CLI command exited before expected timeout\n"
            f"command: {' '.join(command)}\n"
            f"exit: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )

    return _run_cli_until_timeout


@pytest.fixture
def run_cli_bounded(repo_root: Path, isolated_env: dict[str, str]):
    def _run_cli_bounded(
        args: Sequence[str],
        *,
        timeout: float = 5,
        env: Mapping[str, str] | None = None,
    ) -> CliResult:
        merged_env = isolated_env.copy()
        if env:
            merged_env.update(env)

        command = [sys.executable, "-m", "cli.main", *args]
        try:
            completed = subprocess.run(
                command,
                cwd=repo_root,
                env=merged_env,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            return CliResult(
                args=command,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            return CliResult(
                args=command,
                returncode=-1,
                stdout=_to_text(exc.stdout),
                stderr=_to_text(exc.stderr),
            )

    return _run_cli_bounded


@pytest.fixture
def run_script(repo_root: Path, isolated_env: dict[str, str]):
    def _run_script(
        script: str,
        args: Sequence[str] = (),
        *,
        check: bool = True,
        timeout: float = 120,
        env: Mapping[str, str] | None = None,
    ) -> CliResult:
        merged_env = isolated_env.copy()
        if env:
            merged_env.update(env)

        command = [sys.executable, script, *args]
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        result = CliResult(
            args=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            pytest.fail(
                "Script command failed\n"
                f"command: {' '.join(command)}\n"
                f"exit: {result.returncode}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return result

    return _run_script


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("live") and os.environ.get("AGENT_CLI_LIVE_E2E") != "1":
        pytest.skip("set AGENT_CLI_LIVE_E2E=1 to run live E2E tests")
    if item.get_closest_marker("llm") and os.environ.get("AGENT_CLI_LLM_E2E") != "1":
        pytest.skip("set AGENT_CLI_LLM_E2E=1 to run LLM E2E tests")
