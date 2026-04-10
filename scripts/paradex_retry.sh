#!/usr/bin/env bash
# One-command Paradex retry helper.
#
# Default behavior:
#   - activate the local .venv
#   - optionally source an env file
#   - attempt Paradex onboarding (best effort)
#   - run the smoke test
#   - retry until success or attempts are exhausted
#
# Usage examples:
#   bash scripts/paradex_retry.sh
#   PARADEX_RETRY_ATTEMPTS=20 PARADEX_RETRY_DELAY=30 bash scripts/paradex_retry.sh
#   PARADEX_ENV_FILE=.env.paradex bash scripts/paradex_retry.sh
#   bash scripts/paradex_retry.sh -- python scripts/paradex_smoke_test.py

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"
VENV_DIR="${VENV_DIR:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/$VENV_DIR/bin/python}"
ATTEMPTS="${PARADEX_RETRY_ATTEMPTS:-10}"
DELAY="${PARADEX_RETRY_DELAY:-60}"
ENV_FILE="${PARADEX_ENV_FILE:-}"
RUN_ONBOARDING="${PARADEX_RETRY_ONBOARDING:-true}"

usage() {
  cat <<'EOF'
Usage: bash scripts/paradex_retry.sh [-- <command ...>]

Environment knobs:
  PARADEX_RETRY_ATTEMPTS    Number of attempts (default: 10)
  PARADEX_RETRY_DELAY       Seconds between attempts (default: 60)
  PARADEX_ENV_FILE          Optional env file to source before running
  PARADEX_RETRY_ONBOARDING  true/false, best-effort onboarding before smoke test (default: true)
  VENV_DIR                  Virtualenv dir relative to repo root (default: .venv)
  PYTHON_BIN                Override python path used for retries

If no command is supplied, this runs:
  python scripts/paradex_smoke_test.py
EOF
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: PARADEX_ENV_FILE does not exist: $ENV_FILE" >&2
    exit 1
  fi
  echo "[paradex-retry] sourcing env file: $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: python not found at $PYTHON_BIN" >&2
  echo "Hint: create the repo venv first, e.g. python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e ." >&2
  exit 1
fi

if [[ -z "${PARADEX_PRIVATE_KEY:-${PARADEX_L2_PRIVATE_KEY:-}}" ]]; then
  echo "ERROR: set PARADEX_PRIVATE_KEY or PARADEX_L2_PRIVATE_KEY" >&2
  exit 1
fi

if [[ -z "${PARADEX_ADDRESS:-${PARADEX_L2_ADDRESS:-}}" ]]; then
  echo "ERROR: set PARADEX_ADDRESS or PARADEX_L2_ADDRESS" >&2
  exit 1
fi

if [[ "$#" -gt 0 ]]; then
  if [[ "$1" == "--" ]]; then
    shift
  fi
fi

if [[ "$#" -gt 0 ]]; then
  CMD=("$@")
else
  CMD=("$PYTHON_BIN" "scripts/paradex_smoke_test.py")
fi

attempt_onboarding() {
  if ! truthy "$RUN_ONBOARDING"; then
    return 0
  fi

  if [[ -z "${PARADEX_L1_PRIVATE_KEY:-}" ]]; then
    echo "[paradex-retry] skipping onboarding attempt: PARADEX_L1_PRIVATE_KEY not set"
    return 0
  fi

  "$PYTHON_BIN" - <<'PY'
import os
import sys

try:
    from paradex_py import Paradex
except Exception as e:
    print(f"[paradex-retry] onboarding skipped: SDK unavailable: {type(e).__name__}: {e}")
    raise SystemExit(0)

l1_address = os.environ.get("PARADEX_L1_ADDRESS") or os.environ.get("PARADEX_EVM_ADDRESS") or os.environ.get("AGENT_WALLET_ADDRESS")
l1_private_key = os.environ.get("PARADEX_L1_PRIVATE_KEY")
l2_private_key = os.environ.get("PARADEX_PRIVATE_KEY") or os.environ.get("PARADEX_L2_PRIVATE_KEY")
env = "prod" if (os.environ.get("PARADEX_TESTNET", "true").strip().lower() not in {"1", "true", "yes", "on"}) else "testnet"

if not l1_address and l1_private_key:
    try:
        from eth_account import Account
        key = l1_private_key if l1_private_key.startswith("0x") else f"0x{l1_private_key}"
        l1_address = Account.from_key(key).address
    except Exception:
        l1_address = ""

if not l1_address or not l1_private_key or not l2_private_key:
    print("[paradex-retry] onboarding skipped: missing L1 address or keys")
    raise SystemExit(0)

try:
    client = Paradex(
        env=env,
        l1_address=l1_address,
        l1_private_key=l1_private_key,
        l2_private_key=l2_private_key,
    )
    api_client = getattr(client, "api_client", None)
    onboarding = getattr(api_client, "onboarding", None)
    if not callable(onboarding):
        print("[paradex-retry] onboarding skipped: sdk api_client.onboarding() unavailable")
        raise SystemExit(0)
    result = onboarding()
    print(f"[paradex-retry] onboarding call result: {result}")
except Exception as e:
    print(f"[paradex-retry] onboarding call failed: {type(e).__name__}: {e}")
    raise SystemExit(0)
PY
}

echo "[paradex-retry] repo: $ROOT_DIR"
echo "[paradex-retry] python: $PYTHON_BIN"
echo "[paradex-retry] attempts: $ATTEMPTS"
echo "[paradex-retry] delay: ${DELAY}s"
echo "[paradex-retry] command: ${CMD[*]}"

attempt=1
while (( attempt <= ATTEMPTS )); do
  echo ""
  echo "[paradex-retry] ===== attempt $attempt/$ATTEMPTS @ $(date -Iseconds) ====="
  attempt_onboarding

  set +e
  "${CMD[@]}"
  status=$?
  set -e

  if [[ $status -eq 0 ]]; then
    echo "[paradex-retry] success on attempt $attempt"
    exit 0
  fi

  if (( attempt == ATTEMPTS )); then
    echo "[paradex-retry] failed after $ATTEMPTS attempts (last exit code: $status)" >&2
    exit "$status"
  fi

  echo "[paradex-retry] command failed with exit code $status; sleeping ${DELAY}s before retry"
  sleep "$DELAY"
  attempt=$((attempt + 1))
done
