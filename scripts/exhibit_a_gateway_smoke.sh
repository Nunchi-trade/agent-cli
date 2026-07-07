#!/usr/bin/env bash
# Exhibit A gateway smoke — §A.1.7, §A.2 via mcp-gateway POST /mcp/trading
#
# Required env:
#   EXHIBIT_A_MCP_URL   — e.g. https://agent.nunchi.trade/mcp/trading
#   EXHIBIT_A_MCP_TOKEN — bearer from POST /api/mcp/connect
#
# Usage:
#   export EXHIBIT_A_MCP_URL=https://agent.nunchi.trade/mcp/trading
#   export EXHIBIT_A_MCP_TOKEN=tok_...
#   ./scripts/exhibit_a_gateway_smoke.sh read_only
#   ./scripts/exhibit_a_gateway_smoke.sh live_trading  # danger tools need tier + confirmed

set -euo pipefail

TIER="${1:-read_only}"
URL="${EXHIBIT_A_MCP_URL:-}"
TOKEN="${EXHIBIT_A_MCP_TOKEN:-}"
REPORT_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/exhibit-a"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/gateway-smoke-${TIER}-$(date -u +%Y%m%dT%H%M%SZ).jsonl"

if [[ -z "$URL" || -z "$TOKEN" ]]; then
  echo "BLOCKED: set EXHIBIT_A_MCP_URL and EXHIBIT_A_MCP_TOKEN for live gateway smoke" >&2
  echo '{"status":"BLOCKED","reason":"missing_env"}' | tee -a "$REPORT"
  exit 2
fi

call_tool() {
  local name="$1"
  local args="${2:-{}}"
  local id="$RANDOM"
  local body
  body=$(jq -nc --arg n "$name" --argjson a "$args" \
    '{jsonrpc:"2.0",id:($id|tonumber),method:"tools/call",params:{name:$n,arguments:$a}}')
  local resp
  resp=$(curl -sS -X POST "$URL" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$body")
  echo "$resp" | jq -c --arg tool "$name" --arg tier "$TIER" \
    '{tier:$tier,tool:$tool,response:.}' | tee -a "$REPORT"
}

echo "=== Exhibit A gateway smoke (tier=$TIER) ==="
call_tool setup_check
call_tool account '{}'
call_tool funding_rates
call_tool funding_hedge_propose '{"asset":"BTC","perp_notional_usd":1000}'

if [[ "$TIER" != "read_only" ]]; then
  call_tool funding_hedge_execute '{"coin":"BTC","dry_run":true,"mainnet":true,"confirmed":true}'
fi

echo "Report: $REPORT"
