#!/usr/bin/env bash
# Agent-CLI bootstrap — from zero to working `hl` command.
# Usage: bash scripts/bootstrap.sh
set -euo pipefail

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

echo "=== Agent-CLI Bootstrap ==="

# 1. Check Python version (>=3.10)
# Prefer uv if available (auto-reads .python-version)
if command -v uv &>/dev/null; then
    echo "OK  uv detected — using uv for venv"
    USE_UV=1
else
    USE_UV=0
fi

PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "none")
if [ "$PY_VERSION" = "none" ]; then
    echo "ERROR: python3 not found. Install Python 3.10+."
    exit 1
fi

PY_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "ERROR: Python 3.10+ required (found $PY_VERSION)"
    exit 1
fi
echo "OK  Python $PY_VERSION"

# 2. Create venv if not in one
if [ -z "${VIRTUAL_ENV:-}" ]; then
    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating venv at $VENV_DIR ..."
        if [ "$USE_UV" -eq 1 ]; then
            uv venv "$VENV_DIR"
        else
            $PYTHON -m venv "$VENV_DIR"
        fi
    fi
    echo "Activating $VENV_DIR ..."
    source "$VENV_DIR/bin/activate"
else
    echo "OK  Already in venv: $VIRTUAL_ENV"
fi

# 3. Install package
echo "Installing agent-cli ..."
if [ "$USE_UV" -eq 1 ]; then
    uv pip install -e . --quiet 2>&1 | tail -3
else
    pip install -e . --quiet 2>&1 | tail -3
fi

# 4. Verify
echo ""
echo "=== Verification ==="
python3 -m cli.main setup check

echo ""
echo "=== Bootstrap Complete ==="
echo "Activate venv:  source $VENV_DIR/bin/activate"
echo "Next steps:     hl wallet auto  (or hl wallet import --key <key>)"
