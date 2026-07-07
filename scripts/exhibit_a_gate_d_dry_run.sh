#!/usr/bin/env bash
# Exhibit A Gate D dry run — propose → execute → revoke
#
# Prerequisites:
#   - web-auth wallet with minimal mainnet funds + caps configured
#   - EXHIBIT_A_MCP_URL, EXHIBIT_A_MCP_TOKEN (read_only then live_trading)
#   - EXHIBIT_A_PAIR_TOKEN for revoke step
#
# Steps:
#   1. read_only: funding_hedge_propose
#   2. live_trading: funding_hedge_execute (dry_run first, then live if A3 PASS)
#   3. POST /api/pair/revoke on web-auth
#   4. trade must fail

set -euo pipefail

AUTH_URL="${EXHIBIT_A_AUTH_URL:-https://auth.nunchi.trade}"
MCP_URL="${EXHIBIT_A_MCP_URL:-}"
READ_TOKEN="${EXHIBIT_A_MCP_TOKEN_READ:-$EXHIBIT_A_MCP_TOKEN}"
LIVE_TOKEN="${EXHIBIT_A_MCP_TOKEN_LIVE:-}"
PAIR_TOKEN="${EXHIBIT_A_PAIR_TOKEN:-}"
REPORT_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/exhibit-a"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/gate-d-dry-run-$(date -u +%Y%m%dT%H%M%SZ).json"

mcp_call() {
  local token="$1" tool="$2" args="${3:-{}}"
  curl -sS -X POST "$MCP_URL" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg t "$tool" --argjson a "$args" \
      '{jsonrpc:"2.0",id:1,method:"tools/call",params:{name:$t,arguments:$a}}')"
}

if [[ -z "$MCP_URL" || -z "$READ_TOKEN" ]]; then
  jq -n '{gate:"D",status:"BLOCKED",reason:"missing MCP env"}' | tee "$REPORT"
  exit 2
fi

echo "Step 1: propose (read_only)"
PROP=$(mcp_call "$READ_TOKEN" funding_hedge_propose '{"asset":"BTC","perp_notional_usd":1000}')
echo "$PROP" | jq .

if [[ -n "$LIVE_TOKEN" ]]; then
  echo "Step 2: execute dry_run (live_trading)"
  EXEC_DRY=$(mcp_call "$LIVE_TOKEN" funding_hedge_execute \
    '{"coin":"BTC","dry_run":true,"mainnet":true,"confirmed":true}')
  echo "$EXEC_DRY" | jq .
fi

if [[ -n "$PAIR_TOKEN" ]]; then
  echo "Step 3: revoke pair token"
  REVOKE=$(curl -sS -X POST "$AUTH_URL/api/pair/revoke" \
    -H "Authorization: Bearer $PAIR_TOKEN")
  echo "$REVOKE" | jq .
  echo "Step 4: post-revoke trade must fail"
  if [[ -n "$LIVE_TOKEN" ]]; then
    POST=$(mcp_call "$LIVE_TOKEN" trade \
      '{"instrument":"ETH-PERP","side":"buy","size":0.001,"mainnet":true,"confirmed":true}' || true)
    echo "$POST" | jq .
  fi
else
  echo "BLOCKED: set EXHIBIT_A_PAIR_TOKEN for revoke step"
fi

jq -n --arg propose "$PROP" '{gate:"D",status:"RECORDED",propose:$propose}' > "$REPORT"
echo "Report: $REPORT"
