#!/usr/bin/env python3
"""Exhibit A live MCP smoke — local stdio path (§A.1, §A.2 read).

Runs read-only MCP tools against a local hl mcp server subprocess.
For gateway path, use scripts/exhibit_a_gateway_smoke.sh with a bearer token.

Usage:
  .venv/bin/python scripts/exhibit_a_mcp_smoke.py
  .venv/bin/python scripts/exhibit_a_mcp_smoke.py --section a2-danger  # needs EXHIBIT_A_ALLOW_WRITE=1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO_ROOT / "data" / "exhibit-a"


READ_ONLY_TOOLS = [
    ("setup_check", {}),
    ("account", {"mainnet": False}),
    ("status", {}),
    ("funding_rates", {}),
    ("funding_hedge_propose", {"asset": "BTC", "perp_notional_usd": 1000}),
    ("trade_journal", {}),
]

DANGER_TOOLS = [
    ("trade", {"instrument": "ETH-PERP", "side": "buy", "size": 0.001, "mainnet": False, "confirmed": True}),
    ("run_strategy", {"strategy": "cfi_hedge", "instrument": "BTCSWP-PARA", "max_ticks": 1, "dry_run": True, "mock": True}),
    ("funding_hedge_execute", {"coin": "BTC", "dry_run": True}),
]


def call_tool_stdio(tool: str, arguments: dict) -> dict:
    """Invoke MCP tool via direct Python (no subprocess server)."""
    from cli.mcp_server import create_mcp_server

    server = create_mcp_server()
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    if tool not in tools:
        return {"tool": tool, "ok": False, "error": "tool_not_registered"}
    fn = tools[tool].fn
    try:
        result = fn(**arguments)
        return {"tool": tool, "ok": True, "result_preview": str(result)[:500]}
    except Exception as exc:
        return {"tool": tool, "ok": False, "error": str(exc)}


def run_section(name: str, tools: list[tuple[str, dict]]) -> list[dict]:
    results = []
    for tool, args in tools:
        results.append(call_tool_stdio(tool, args))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", choices=["a1", "a2-read", "a2-danger", "all"], default="a2-read")
    args = parser.parse_args()

    sections: list[tuple[str, list]] = []
    if args.section in ("a1", "all"):
        sections.append(("A1.1_local_mcp", READ_ONLY_TOOLS[:1]))
    if args.section in ("a2-read", "all"):
        sections.append(("A2_read_only", READ_ONLY_TOOLS))
    if args.section in ("a2-danger", "all"):
        if os.getenv("EXHIBIT_A_ALLOW_WRITE") != "1":
            print("Set EXHIBIT_A_ALLOW_WRITE=1 for danger-section dry-run tools", file=sys.stderr)
        sections.append(("A2_danger_dry_run", DANGER_TOOLS))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"mcp-smoke-{args.section}.json"
    payload = {"sections": {}}

    exit_code = 0
    for section_name, tools in sections:
        results = run_section(section_name, tools)
        payload["sections"][section_name] = results
        for row in results:
            status = "PASS" if row.get("ok") else "FAIL"
            print(f"[{status}] {section_name} :: {row.get('tool')}")
            if not row.get("ok"):
                exit_code = 1
                print(f"       error: {row.get('error')}")

    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nReport: {report_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
