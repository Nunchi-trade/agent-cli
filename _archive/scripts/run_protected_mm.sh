#!/bin/bash
# Launch protected MM on YEX testnet with markout tracking.
#
# Usage:
#   ./scripts/run_protected_mm.sh us3m    # Run US3M MM
#   ./scripts/run_protected_mm.sh vxx     # Run VXX MM
#   ./scripts/run_protected_mm.sh both    # Run both (separate processes)
#
# Prerequisites:
#   export HL_PRIVATE_KEY=0x...

set -euo pipefail

cd "$(dirname "$0")/.."
AGENT_CLI_DIR="$(pwd)"
VENV_PYTHON="${HOME}/hl-anomaly-detector/.venv/bin/python"
LOG_DIR="${AGENT_CLI_DIR}/logs"
DATE=$(date +%Y%m%d)

mkdir -p "$LOG_DIR"

if [ -z "${HL_PRIVATE_KEY:-}" ]; then
    echo "ERROR: HL_PRIVATE_KEY not set. Run: export HL_PRIVATE_KEY=0x..."
    exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    echo "Run: cd ~/hl-anomaly-detector && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

launch_market() {
    local market=$1
    local config="configs/yex_${market}_protected.yaml"
    local instrument

    case $market in
        us3m) instrument="US3M-USDYP" ;;
        vxx)  instrument="VXX-USDYP" ;;
        *)    echo "Unknown market: $market"; exit 1 ;;
    esac

    if [ ! -f "$config" ]; then
        echo "ERROR: Config not found: $config"
        exit 1
    fi

    local log_file="${LOG_DIR}/${market}-mm-${DATE}.log"

    echo "Launching $market MM..."
    echo "  Config:     $config"
    echo "  Instrument: $instrument"
    echo "  Log:        $log_file"
    echo "  Python:     $VENV_PYTHON"

    PYTHONPATH="${AGENT_CLI_DIR}:${HOME}/anomaly-protection" \
    HL_PRIVATE_KEY="$HL_PRIVATE_KEY" \
    nohup "$VENV_PYTHON" cli/main.py run avellaneda_mm \
        -i "$instrument" \
        -c "$config" \
        --fresh \
        >> "$log_file" 2>&1 &

    local pid=$!
    echo "  PID:        $pid"
    echo "$pid" > "${LOG_DIR}/${market}-mm.pid"
    echo ""
}

case "${1:-both}" in
    us3m)
        launch_market us3m
        ;;
    vxx)
        launch_market vxx
        ;;
    both)
        launch_market us3m
        sleep 2
        launch_market vxx
        ;;
    stop)
        for market in us3m vxx; do
            pidfile="${LOG_DIR}/${market}-mm.pid"
            if [ -f "$pidfile" ]; then
                pid=$(cat "$pidfile")
                if kill -0 "$pid" 2>/dev/null; then
                    kill "$pid"
                    echo "Stopped $market MM (PID $pid)"
                else
                    echo "$market MM not running (PID $pid)"
                fi
                rm -f "$pidfile"
            fi
        done
        ;;
    status)
        for market in us3m vxx; do
            pidfile="${LOG_DIR}/${market}-mm.pid"
            if [ -f "$pidfile" ]; then
                pid=$(cat "$pidfile")
                if kill -0 "$pid" 2>/dev/null; then
                    echo "$market MM: RUNNING (PID $pid)"
                    tail -1 "${LOG_DIR}/${market}-mm-${DATE}.log" 2>/dev/null
                else
                    echo "$market MM: STOPPED"
                fi
            else
                echo "$market MM: NOT STARTED"
            fi
        done

        # Show markout stats
        for market in us3m vxx; do
            markout="data/yex-${market}/markouts.jsonl"
            if [ -f "$markout" ]; then
                count=$(wc -l < "$markout")
                echo ""
                echo "$market markouts: $count records"
            fi
        done
        ;;
    analyze)
        echo "=== US3M Markout Analysis ==="
        PYTHONPATH="${HOME}/anomaly-protection" \
        "$VENV_PYTHON" "${HOME}/anomaly-protection/analyze_markouts.py" \
            "data/yex-us3m/markouts.jsonl" 2>/dev/null || echo "No US3M data yet"

        echo ""
        echo "=== VXX Markout Analysis ==="
        PYTHONPATH="${HOME}/anomaly-protection" \
        "$VENV_PYTHON" "${HOME}/anomaly-protection/analyze_markouts.py" \
            "data/yex-vxx/markouts.jsonl" 2>/dev/null || echo "No VXX data yet"
        ;;
    *)
        echo "Usage: $0 {us3m|vxx|both|stop|status|analyze}"
        exit 1
        ;;
esac
