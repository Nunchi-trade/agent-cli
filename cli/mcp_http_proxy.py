"""Forward MCP JSON-RPC requests to an upstream FastMCP HTTP endpoint."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping


def forward_mcp_json_rpc(
    raw_body: bytes,
    headers: Mapping[str, str],
    upstream_url: str | None = None,
    timeout: float = 120.0,
) -> tuple[int, dict[str, Any]]:
    url = upstream_url or os.environ.get("MCP_UPSTREAM_URL", "http://127.0.0.1:8765/mcp")
    req_headers = {
        "content-type": "application/json",
        "accept": "application/json",
    }
    for key, value in headers.items():
        lower = str(key).lower()
        if lower.startswith("x-nunchi-") or lower == "authorization":
            req_headers[str(key)] = str(value)

    request = urllib.request.Request(
        url,
        data=raw_body,
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            payload = {"error": {"code": -32000, "message": body or f"upstream HTTP {exc.code}"}}
        return exc.code, payload
    except Exception as exc:
        return 502, {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32000, "message": f"upstream MCP unavailable: {exc}"},
        }
