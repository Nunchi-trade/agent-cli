#!/usr/bin/env python3
"""Comprehensive agent-cli validation runner.

Profiles:
  quick       Fast local sanity checks: imports, compile, focused pytest, CLI help.
  full        All local test cases plus static/CLI/Node checks.
  e2e         full + non-destructive mock CLI flows in an isolated HOME/data dir.
  production  e2e + read-only live exchange probes, gated by explicit opt-in.

Safety:
  - No profile places orders, approves builder fees, or mutates live account state.
  - Mock/e2e flows run with a temporary HOME and HL_TESTNET=true.
  - production refuses to run live probes unless --allow-live or
    AGENT_CLI_ALLOW_LIVE=1 is set.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_DIR = REPO_ROOT / "data" / "validation"

SECRET_PATTERNS = [
    re.compile(r"(0x)?[0-9a-fA-F]{64}"),
    re.compile(r'("password"\s*:\s*")[^"]+(")'),
    re.compile(r"(HL_PRIVATE_KEY=)[^\s]+"),
    re.compile(r"(HL_KEYSTORE_PASSWORD=)[^\s]+"),
    re.compile(r"(Authorization:\s*Bearer\s+)[^\s]+", re.IGNORECASE),
]


@dataclass(frozen=True)
class TestCase:
    name: str
    stage: str
    command: list[str]
    timeout_s: int = 60
    expected_codes: tuple[int, ...] = (0,)
    profiles: tuple[str, ...] = ("quick", "full", "e2e", "production")
    requires_executable: str | None = None
    live_probe: bool = False
    optional: bool = False
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class TestResult:
    name: str
    stage: str
    command: str
    status: str
    exit_code: int | None
    elapsed_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    reason: str = ""


def redact(text: str) -> str:
    out = text
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith('("password"'):
            out = pattern.sub(r"\1<redacted>\2", out)
        elif "Authorization" in pattern.pattern:
            out = pattern.sub(r"\1<redacted>", out)
        else:
            out = pattern.sub("<redacted>", out)
    return out


def tail(text: str, max_chars: int = 4000) -> str:
    text = redact(text.strip())
    if len(text) <= max_chars:
        return text
    return "...<truncated>...\n" + text[-max_chars:]


def python_cmd(args: Sequence[str], python: str) -> list[str]:
    return [python, *args]


def base_cases(python: str, quick_pytest: bool) -> list[TestCase]:
    pytest_target = ("tests",) if not quick_pytest else (
        "tests/test_config.py",
        "tests/test_credentials.py",
        "tests/test_strategy_registry.py",
        "tests/test_hl_adapter.py",
        "tests/test_engine.py",
    )

    cases: list[TestCase] = [
        TestCase(
            name="python_version",
            stage="preflight",
            command=python_cmd(["-c", "import sys; assert sys.version_info >= (3, 10); print(sys.version)"], python),
            timeout_s=10,
        ),
        TestCase(
            name="package_imports",
            stage="preflight",
            command=python_cmd(["-c", "import cli.main, common.models, parent.risk_manager; print('imports ok')"], python),
            timeout_s=20,
        ),
        TestCase(
            name="compile_python_sources",
            stage="static",
            command=python_cmd(
                [
                    "-m",
                    "compileall",
                    "-q",
                    "cli",
                    "common",
                    "parent",
                    "sdk",
                    "strategies",
                    "modules",
                    "skills",
                    "quoting_engine",
                    "scripts",
                ],
                python,
            ),
            timeout_s=120,
        ),
        TestCase(
            name="pytest_all_cases" if not quick_pytest else "pytest_quick_core",
            stage="pytest",
            command=python_cmd(["-m", "pytest", *pytest_target], python),
            timeout_s=900 if not quick_pytest else 180,
            profiles=("quick", "full", "e2e", "production"),
        ),
        TestCase(
            name="cli_root_help",
            stage="cli_help",
            command=python_cmd(["-m", "cli.main", "--help"], python),
            timeout_s=20,
        ),
    ]

    for command in (
        "strategies",
        "setup",
        "wallet",
        "builder",
        "radar",
        "pulse",
        "apex",
        "guard",
        "reflect",
        "journal",
        "keys",
        "mcp",
        "skills",
    ):
        cases.append(
            TestCase(
                name=f"cli_{command}_help",
                stage="cli_help",
                command=python_cmd(["-m", "cli.main", command, "--help"], python),
                timeout_s=20,
            )
        )

    return cases


def node_cases() -> list[TestCase]:
    cases: list[TestCase] = []
    for path in sorted((REPO_ROOT / "deploy").glob("*/src/*")):
        if path.suffix not in {".js", ".mjs"}:
            continue
        cases.append(
            TestCase(
                name=f"node_check_{path.parent.parent.name}_{path.name}",
                stage="node_static",
                command=["node", "--check", str(path.relative_to(REPO_ROOT))],
                timeout_s=20,
                profiles=("full", "e2e", "production"),
                requires_executable="node",
                optional=True,
            )
        )
    return cases


def e2e_cases(python: str, data_root: Path) -> list[TestCase]:
    cli_data = data_root / "cli"
    return [
        TestCase(
            name="setup_check_sandbox",
            stage="e2e_mock",
            command=python_cmd(["-m", "cli.main", "setup", "check"], python),
            timeout_s=30,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="strategies_catalog",
            stage="e2e_mock",
            command=python_cmd(["-m", "cli.main", "strategies"], python),
            timeout_s=30,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="wallet_auto_sandbox",
            stage="e2e_mock",
            command=python_cmd(["-m", "cli.main", "wallet", "auto", "--json", "--save-env"], python),
            timeout_s=30,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="wallet_list_sandbox",
            stage="e2e_mock",
            command=python_cmd(["-m", "cli.main", "wallet", "list"], python),
            timeout_s=30,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="builder_status_local",
            stage="e2e_mock",
            command=python_cmd(["-m", "cli.main", "builder", "status"], python),
            timeout_s=30,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="run_simple_mm_mock",
            stage="e2e_mock",
            command=python_cmd(
                [
                    "-m",
                    "cli.main",
                    "run",
                    "simple_mm",
                    "--mock",
                    "--max-ticks",
                    "1",
                    "--tick",
                    "0.01",
                    "--fresh",
                    "--data-dir",
                    str(cli_data),
                ],
                python,
            ),
            timeout_s=60,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="radar_once_mock",
            stage="e2e_mock",
            command=python_cmd(
                [
                    "-m",
                    "cli.main",
                    "radar",
                    "once",
                    "--mock",
                    "--top-n",
                    "3",
                    "--min-volume",
                    "0",
                    "--score-threshold",
                    "0",
                    "--data-dir",
                    str(data_root / "radar"),
                ],
                python,
            ),
            timeout_s=60,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="pulse_once_mock",
            stage="e2e_mock",
            command=python_cmd(
                [
                    "-m",
                    "cli.main",
                    "pulse",
                    "once",
                    "--mock",
                    "--min-volume",
                    "0",
                    "--data-dir",
                    str(data_root / "pulse"),
                ],
                python,
            ),
            timeout_s=60,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="apex_once_mock",
            stage="e2e_mock",
            command=python_cmd(
                ["-m", "cli.main", "apex", "once", "--mock", "--data-dir", str(data_root / "apex")],
                python,
            ),
            timeout_s=90,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="apex_status_after_mock",
            stage="e2e_mock",
            command=python_cmd(["-m", "cli.main", "apex", "status", "--data-dir", str(data_root / "apex")], python),
            timeout_s=30,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="status_reader_json_contracts",
            stage="api_contract",
            command=python_cmd(["-c", status_reader_contract_code(data_root)], python),
            timeout_s=60,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="entrypoint_http_readonly_contracts",
            stage="api_contract",
            command=python_cmd(["-c", entrypoint_http_contract_code()], python),
            timeout_s=60,
            profiles=("e2e", "production"),
        ),
        TestCase(
            name="mcp_server_factory_gate",
            stage="mcp",
            command=python_cmd(["-c", mcp_factory_gate_code()], python),
            timeout_s=30,
            profiles=("e2e", "production"),
            optional=True,
        ),
    ]


def status_reader_contract_code(data_root: Path) -> str:
    data = json.dumps(str(data_root))
    return (
        "import json, subprocess, sys; "
        f"data_dir={data}; "
        "cmds=['status','strategies','trades','reflect','radar','journal']; "
        "seen={}; "
        "\nfor cmd in cmds:\n"
        "    args=[sys.executable,'-m','cli.api.status_reader',cmd,'--data-dir',data_dir]\n"
        "    if cmd in ('trades','journal'): args += ['--limit','5']\n"
        "    payload=json.loads(subprocess.check_output(args, text=True))\n"
        "    assert isinstance(payload, dict), cmd\n"
        "    seen[cmd]=sorted(payload.keys())\n"
        "assert 'status' in seen['status']\n"
        "assert 'strategies' in seen['strategies']\n"
        "assert 'trades' in seen['trades'] and 'total' in seen['trades']\n"
        "assert 'report' in seen['reflect']\n"
        "assert 'scans' in seen['radar']\n"
        "assert 'entries' in seen['journal']\n"
        "print(json.dumps(seen, sort_keys=True))"
    )


def entrypoint_http_contract_code() -> str:
    return (
        "import json, threading, urllib.request; "
        "from http.server import HTTPServer; "
        "from socketserver import ThreadingMixIn; "
        "from scripts.entrypoint import HealthHandler; "
        "\nclass Server(ThreadingMixIn, HTTPServer):\n"
        "    daemon_threads = True\n"
        "\nserver = Server(('127.0.0.1', 0), HealthHandler)\n"
        "thread = threading.Thread(target=server.serve_forever, daemon=True)\n"
        "thread.start()\n"
        "base = f'http://127.0.0.1:{server.server_address[1]}'\n"
        "try:\n"
        "    health = json.loads(urllib.request.urlopen(base + '/health', timeout=5).read())\n"
        "    assert health['status'] == 'ok'\n"
        "    status = json.loads(urllib.request.urlopen(base + '/api/status', timeout=5).read())\n"
        "    assert 'status' in status\n"
        "    strategies = json.loads(urllib.request.urlopen(base + '/api/strategies', timeout=5).read())\n"
        "    assert 'strategies' in strategies\n"
        "    metrics = json.loads(urllib.request.urlopen(base + '/metrics', timeout=5).read())\n"
        "    assert isinstance(metrics, dict)\n"
        "    print(json.dumps({'health': health['status'], 'api_status': status['status']}))\n"
        "finally:\n"
        "    server.shutdown()\n"
        "    server.server_close()\n"
    )


def mcp_factory_gate_code() -> str:
    return (
        "try:\n"
        "    from cli.mcp_server import create_mcp_server\n"
        "    create_mcp_server()\n"
        "    print('mcp server factory ok')\n"
        "except ModuleNotFoundError as exc:\n"
        "    if exc.name == 'mcp':\n"
        "        print('optional mcp package not installed; factory gate skipped')\n"
        "    else:\n"
        "        raise\n"
    )


def production_cases(python: str, mainnet: bool) -> list[TestCase]:
    network_flag = ["--mainnet"] if mainnet else []
    return [
        TestCase(
            name="live_account_read",
            stage="production_readonly",
            command=python_cmd(["-m", "cli.main", "account", *network_flag], python),
            timeout_s=45,
            profiles=("production",),
            live_probe=True,
        ),
        TestCase(
            name="live_status_read",
            stage="production_readonly",
            command=python_cmd(["-m", "cli.main", "status"], python),
            timeout_s=45,
            profiles=("production",),
            live_probe=True,
        ),
        TestCase(
            name="live_radar_readonly_scan",
            stage="production_readonly",
            command=python_cmd(
                [
                    "-m",
                    "cli.main",
                    "radar",
                    "once",
                    *network_flag,
                    "--top-n",
                    "5",
                    "--score-threshold",
                    "9999",
                ],
                python,
            ),
            timeout_s=90,
            profiles=("production",),
            live_probe=True,
        ),
    ]


def profile_includes(profile: str, case: TestCase) -> bool:
    if profile == "quick":
        return "quick" in case.profiles
    if profile == "full":
        return "full" in case.profiles
    if profile == "e2e":
        return "e2e" in case.profiles
    return "production" in case.profiles


def command_string(command: Sequence[str]) -> str:
    return " ".join(shlex_quote(part) for part in command)


def shlex_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def executable_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_case(case: TestCase, env: dict[str, str]) -> TestResult:
    if case.requires_executable and not executable_available(case.requires_executable):
        return TestResult(
            name=case.name,
            stage=case.stage,
            command=command_string(case.command),
            status="skipped" if case.optional else "failed",
            exit_code=None,
            elapsed_s=0.0,
            reason=f"missing executable: {case.requires_executable}",
        )

    merged_env = dict(env)
    merged_env.update(case.env)
    started = time.time()
    try:
        proc = subprocess.run(
            case.command,
            cwd=REPO_ROOT,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=case.timeout_s,
        )
        elapsed = time.time() - started
    except subprocess.TimeoutExpired as exc:
        return TestResult(
            name=case.name,
            stage=case.stage,
            command=command_string(case.command),
            status="failed",
            exit_code=None,
            elapsed_s=time.time() - started,
            stdout_tail=tail(exc.stdout or ""),
            stderr_tail=tail(exc.stderr or ""),
            reason=f"timed out after {case.timeout_s}s",
        )

    ok = proc.returncode in case.expected_codes
    return TestResult(
        name=case.name,
        stage=case.stage,
        command=command_string(case.command),
        status="passed" if ok else "failed",
        exit_code=proc.returncode,
        elapsed_s=elapsed,
        stdout_tail=tail(proc.stdout),
        stderr_tail=tail(proc.stderr),
        reason="" if ok else f"expected exit code in {case.expected_codes}",
    )


def build_env(home: Path, data_root: Path, mainnet: bool) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "HL_TESTNET": "false" if mainnet else "true",
            "PYTHONUNBUFFERED": "1",
            "NUNCHI_VALIDATION": "1",
            "DATA_DIR": str(data_root),
        }
    )
    return env


def write_report(results: list[TestResult], report_path: Path, profile: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile": profile,
        "repo": str(REPO_ROOT),
        "generated_at_ms": int(time.time() * 1000),
        "summary": summarize(results),
        "results": [asdict(r) for r in results],
    }
    report_path.write_text(json.dumps(payload, indent=2))


def summarize(results: Iterable[TestResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def print_result(result: TestResult) -> None:
    marker = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[result.status]
    print(f"[{marker}] {result.stage}/{result.name} ({result.elapsed_s:.1f}s)")
    if result.reason:
        print(f"       {result.reason}")
    if result.status == "failed":
        if result.stdout_tail:
            print("       stdout:")
            print(indent(result.stdout_tail))
        if result.stderr_tail:
            print("       stderr:")
            print(indent(result.stderr_tail))


def indent(text: str) -> str:
    return "\n".join(f"         {line}" for line in text.splitlines())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "full", "e2e", "production"), default="full")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter used for checks")
    parser.add_argument("--quick-pytest", action="store_true", help="Run the focused pytest subset even outside quick profile")
    parser.add_argument("--allow-live", action="store_true", help="Allow production read-only live probes")
    parser.add_argument("--mainnet", action="store_true", help="Use mainnet for production live probes")
    parser.add_argument("--report", default="", help="JSON report path (default: data/validation/<profile>-<ts>.json)")
    parser.add_argument("--list", action="store_true", help="List selected test cases without running them")
    parser.add_argument("--keep-sandbox", action="store_true", help="Keep temporary HOME/data directory for debugging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quick_pytest = args.profile == "quick" or args.quick_pytest
    allow_live = args.allow_live or os.environ.get("AGENT_CLI_ALLOW_LIVE") == "1"

    if args.profile == "production" and not allow_live:
        print(
            "Refusing production profile without explicit live-read opt-in.\n"
            "Re-run with --allow-live or AGENT_CLI_ALLOW_LIVE=1. "
            "The production profile is read-only, but it still connects to live exchange APIs.",
            file=sys.stderr,
        )
        return 2

    sandbox = tempfile.TemporaryDirectory(prefix="agent-cli-validation-")
    sandbox_root = Path(sandbox.name)
    home = sandbox_root / "home"
    data_root = sandbox_root / "data"
    home.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    try:
        cases = [
            *base_cases(args.python, quick_pytest=quick_pytest),
            *node_cases(),
            *e2e_cases(args.python, data_root),
            *production_cases(args.python, mainnet=args.mainnet),
        ]
        selected = [case for case in cases if profile_includes(args.profile, case)]

        if args.list:
            for case in selected:
                live = " live" if case.live_probe else ""
                print(f"{case.stage}/{case.name}{live}: {command_string(case.command)}")
            return 0

        env = build_env(home, data_root, mainnet=args.mainnet)
        print(f"agent-cli validation profile={args.profile} repo={REPO_ROOT}")
        print(f"sandbox={sandbox_root}")
        print(f"cases={len(selected)}")

        results: list[TestResult] = []
        for case in selected:
            result = run_case(case, env)
            results.append(result)
            print_result(result)

        ts = int(time.time())
        report_path = Path(args.report) if args.report else DEFAULT_REPORT_DIR / f"{args.profile}-{ts}.json"
        write_report(results, report_path, args.profile)

        summary = summarize(results)
        print(f"\nSummary: {summary} | report={report_path}")
        failed = summary.get("failed", 0)
        return 1 if failed else 0
    finally:
        if args.keep_sandbox:
            print(f"Kept sandbox at {sandbox_root}")
        else:
            sandbox.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
