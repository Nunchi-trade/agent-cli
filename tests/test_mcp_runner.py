"""Tests for cli/mcp_runner JSON-RPC helpers."""
import json

from cli.mcp_runner import handle_mcp_json_rpc


def test_handle_mcp_json_rpc_parse_error():
    status, payload = handle_mcp_json_rpc(b"{not-json", {})
    assert status == 400
    assert payload["error"]["code"] == -32700


def test_handle_mcp_json_rpc_initialize():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode()
    status, payload = handle_mcp_json_rpc(body, {})
    assert status == 200
    assert payload["result"]["serverInfo"]["name"] == "nunchi-agent-cli-runner"
