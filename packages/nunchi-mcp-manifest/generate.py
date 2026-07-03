#!/usr/bin/env python3
"""Generate MCP tool manifest constants from packages/nunchi-mcp-manifest/tools.json.

Usage:
  python packages/nunchi-mcp-manifest/generate.py
  python packages/nunchi-mcp-manifest/generate.py --check
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).resolve().parent / "tools.json"
OUTPUT_PATH = ROOT / "cli" / "generated" / "mcp_tool_manifest.py"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def bucket_tools(manifest: dict, bucket: str) -> list[str]:
    return sorted(name for name, meta in manifest["tools"].items() if meta["bucket"] == bucket)


def runner_tools(manifest: dict) -> list[str]:
    return sorted(name for name, meta in manifest["tools"].items() if meta.get("runner"))


def read_only_tools(manifest: dict) -> list[str]:
    return sorted(name for name, meta in manifest["tools"].items() if meta.get("readOnly"))


def destructive_tools(manifest: dict) -> list[str]:
    return sorted(name for name, meta in manifest["tools"].items() if meta.get("destructive"))


def render_py(manifest: dict) -> str:
    read_only = read_only_tools(manifest)
    destructive = destructive_tools(manifest)
    buckets = {
        "free": bucket_tools(manifest, "free"),
        "paidCompute": bucket_tools(manifest, "paidCompute"),
        "safetyGated": bucket_tools(manifest, "safetyGated"),
    }
    runner = runner_tools(manifest)
    metadata_json = json.dumps(manifest["tools"], indent=4)

    return f'''"""GENERATED FROM packages/nunchi-mcp-manifest/tools.json — DO NOT EDIT.

Run: python packages/nunchi-mcp-manifest/generate.py
"""

from __future__ import annotations

import json

MANIFEST_VERSION = {manifest["version"]}

TOOL_BUCKETS: dict[str, list[str]] = {json.dumps(buckets, indent=4)}

FREE_QUOTA_EXECUTION_TOOLS: tuple[str, ...] = {tuple(manifest["freeQuotaExecution"])!r}

READ_ONLY_TOOLS = frozenset({read_only!r})

DESTRUCTIVE_TOOLS = frozenset({destructive!r})

RUNNER_TOOLS: tuple[str, ...] = {tuple(runner)!r}

TOOL_METADATA: dict[str, dict] = json.loads({metadata_json!r})
'''


def main() -> int:
    check = "--check" in sys.argv
    manifest = load_manifest()
    rendered = render_py(manifest)

    if check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text() != rendered:
            print("Generated mcp_tool_manifest.py is out of date. Run: python packages/nunchi-mcp-manifest/generate.py", file=sys.stderr)
            return 1
        print("mcp_tool_manifest.py is up to date")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered)
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
