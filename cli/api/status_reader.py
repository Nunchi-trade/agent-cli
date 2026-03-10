"""Read agent status from StateDB and WOLF state files.

Shared utility used by:
- scripts/entrypoint.py (imported directly)
- deploy/openclaw-railway/src/server.js (via `python3 -m cli.api.status_reader`)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def read_status(data_dir: str = "data") -> Dict[str, Any]:
    """Read unified agent status from StateDB + WOLF state.json.

    Checks both `{data_dir}/cli/state.db` (TradingEngine)
    and `{data_dir}/wolf/state.json` (WOLF orchestrator).
    """
    result: Dict[str, Any] = {"status": "stopped"}

    # Try WOLF state first (higher priority — WOLF wraps strategies)
    wolf_state = _read_wolf_state(f"{data_dir}/wolf")
    if wolf_state:
        result.update(wolf_state)
        result["status"] = "running"
        return result

    # Fall back to single-strategy StateDB
    engine_state = _read_engine_state(f"{data_dir}/cli")
    if engine_state:
        result.update(engine_state)
        result["status"] = "running"
        return result

    return result


def _read_wolf_state(wolf_dir: str) -> Dict[str, Any] | None:
    """Read WOLF orchestrator state from state.json."""
    state_path = Path(wolf_dir) / "state.json"
    if not state_path.exists():
        return None

    try:
        with open(state_path) as f:
            state = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    active_slots = [s for s in state.get("slots", []) if s.get("status") == "active"]
    closed_slots = [s for s in state.get("slots", []) if s.get("status") == "closed"]

    metrics = _read_trade_metrics(wolf_dir)
    account = _read_account(wolf_dir)
    override = _read_config_override(wolf_dir)

    return {
        "engine": "wolf",
        "tick_count": state.get("tick_count", 0),
        "daily_pnl": state.get("daily_pnl", 0.0),
        "total_pnl": state.get("total_pnl", 0.0),
        "total_trades": state.get("total_trades", 0),
        "max_slots": state.get("max_slots", 3),
        "active_slots": active_slots,
        "closed_slots": closed_slots[-5:],  # last 5 closed
        "positions": [
            {
                "slot": s.get("slot_id"),
                "market": s.get("instrument", ""),
                "side": s.get("side", ""),
                "size": s.get("entry_size", 0),
                "entry": s.get("entry_price", 0),
                "roe": s.get("roe_pct", 0),
                "phase": s.get("guard_phase", 0),
            }
            for s in active_slots
        ],
        "win_rate": metrics.get("win_rate"),
        "volume": metrics.get("volume"),
        "fee_total": metrics.get("fee_total"),
        "risk": {
            "daily_drawdown": state.get("daily_pnl", 0),
            "safe_mode": state.get("daily_loss_triggered", False),
            "reduce_only": False,
            "safe_mode_reason": None,
        },
        "account": account,
        "preset": override.get("preset") or state.get("preset", "default"),
        "network": "testnet" if os.environ.get("HL_TESTNET", "true").lower() == "true" else "mainnet",
    }


def _read_trade_metrics(wolf_dir: str) -> Dict[str, Any]:
    """Compute trade metrics from trades.jsonl."""
    trades_path = Path(wolf_dir) / "trades.jsonl"
    if not trades_path.exists():
        return {}

    try:
        trades = []
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))

        if not trades:
            return {}

        # Pair entries/exits to compute PnL per round-trip
        # For simpler accounting: look at exit trades (meta starts with exit reasons)
        # or compute from fee/price data
        volume = 0.0
        fee_total = 0.0
        wins = 0
        total_exits = 0

        for t in trades:
            price = float(t.get("price", 0))
            qty = float(t.get("quantity", 0))
            notional = price * qty
            volume += notional

            fee = float(t.get("fee", 0))
            if fee == 0:
                fee = notional * 0.001  # estimate 0.1%
            fee_total += fee

        # Win rate: pair trades by instrument (entry then exit)
        # Group by instrument, pair chronologically
        by_instrument: Dict[str, list] = {}
        for t in trades:
            inst = t.get("instrument", "")
            by_instrument.setdefault(inst, []).append(t)

        for inst, inst_trades in by_instrument.items():
            i = 0
            while i + 1 < len(inst_trades):
                entry = inst_trades[i]
                exit_t = inst_trades[i + 1]
                entry_price = float(entry.get("price", 0))
                exit_price = float(exit_t.get("price", 0))
                entry_side = entry.get("side", "")

                if entry_price > 0 and exit_price > 0:
                    if entry_side == "buy":
                        pnl = exit_price - entry_price
                    else:
                        pnl = entry_price - exit_price
                    if pnl > 0:
                        wins += 1
                    total_exits += 1

                i += 2

        win_rate = (wins / total_exits * 100) if total_exits > 0 else 0.0

        return {"win_rate": round(win_rate, 2), "volume": round(volume, 2), "fee_total": round(fee_total, 4)}
    except Exception:
        return {}


def _read_account(wolf_dir: str) -> Dict[str, Any] | None:
    """Read account.json if it exists."""
    account_path = Path(wolf_dir) / "account.json"
    if not account_path.exists():
        return None
    try:
        with open(account_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _read_config_override(wolf_dir: str) -> Dict[str, Any]:
    """Read config-override.json if it exists."""
    override_path = Path(wolf_dir) / "config-override.json"
    if not override_path.exists():
        return {}
    try:
        with open(override_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _read_engine_state(cli_dir: str) -> Dict[str, Any] | None:
    """Read single-strategy state from StateDB."""
    db_path = Path(cli_dir) / "state.db"
    if not db_path.exists():
        return None

    # Import here to avoid top-level dependency issues when run standalone
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from parent.store import StateDB

        db = StateDB(path=str(db_path))
        try:
            tick_count = db.get("tick_count") or 0
            strategy_id = db.get("strategy_id") or ""
            instrument = db.get("instrument") or ""
            order_stats = db.get("order_stats") or {}
            positions_data = db.get("positions")

            pos_qty = 0.0
            upnl = 0.0
            rpnl = 0.0
            if positions_data:
                for _agent_id, instruments in positions_data.get("agents", {}).items():
                    for _inst, pos in instruments.items():
                        pos_qty = float(pos.get("net_qty", "0"))
                        upnl = float(pos.get("unrealized_pnl", "0"))
                        rpnl = float(pos.get("realized_pnl", "0"))

            return {
                "engine": strategy_id,
                "tick_count": tick_count,
                "instrument": instrument,
                "position_qty": pos_qty,
                "unrealized_pnl": upnl,
                "realized_pnl": rpnl,
                "total_orders": order_stats.get("total_placed", 0),
                "total_fills": order_stats.get("total_filled", 0),
            }
        finally:
            db.close()
    except Exception:
        return None


def read_strategies() -> Dict[str, Any]:
    """Return strategy catalog from strategy_registry."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.strategy_registry import STRATEGY_REGISTRY, YEX_MARKETS

    strategies = {}
    for name, info in STRATEGY_REGISTRY.items():
        strategies[name] = {
            "description": info["description"],
            "params": info["params"],
        }

    return {
        "strategies": strategies,
        "markets": {
            name: info["description"]
            for name, info in YEX_MARKETS.items()
        },
    }


def read_trades(data_dir: str, limit: int = 50) -> Dict[str, Any]:
    """Read trades from trades.jsonl, newest-first, limited."""
    trades_path = Path(data_dir) / "wolf" / "trades.jsonl"
    if not trades_path.exists():
        return {"trades": [], "total": 0}

    try:
        trades = []
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
        total = len(trades)
        trades.reverse()  # newest first
        return {"trades": trades[:limit], "total": total}
    except Exception:
        return {"trades": [], "total": 0}


def read_reflect(data_dir: str) -> Dict[str, Any]:
    """Read latest REFLECT report from reflect/ directory."""
    reflect_dir = Path(data_dir) / "wolf" / "reflect"
    if not reflect_dir.exists():
        return {"report": None, "report_name": None, "reports": []}

    try:
        md_files = sorted(reflect_dir.glob("*.md"), key=lambda p: p.name, reverse=True)
        reports = [f.name for f in md_files]
        if md_files:
            latest = md_files[0]
            return {
                "report": latest.read_text(),
                "report_name": latest.name,
                "reports": reports,
            }
        return {"report": None, "report_name": None, "reports": reports}
    except Exception:
        return {"report": None, "report_name": None, "reports": []}


def read_scanner(data_dir: str) -> Dict[str, Any]:
    """Read radar history."""
    primary = Path(data_dir) / "wolf" / "radar-history.json"
    fallback = Path(data_dir) / "radar" / "scan-history.json"

    scan_path = primary if primary.exists() else (fallback if fallback.exists() else None)
    if scan_path is None:
        return {"scans": [], "latest": None}

    try:
        with open(scan_path) as f:
            scans = json.load(f)
        if not isinstance(scans, list):
            scans = [scans]
        latest = scans[-1] if scans else None
        return {"scans": scans, "latest": latest}
    except Exception:
        return {"scans": [], "latest": None}


def read_journal(data_dir: str, limit: int = 50) -> Dict[str, Any]:
    """Read journal entries, newest-first."""
    journal_path = Path(data_dir) / "wolf" / "journal.jsonl"
    if not journal_path.exists():
        return {"entries": [], "total": 0}

    try:
        entries = []
        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        total = len(entries)
        entries.reverse()  # newest first
        return {"entries": entries[:limit], "total": total}
    except Exception:
        return {"entries": [], "total": 0}


def write_config_override(data_dir: str, config: Dict[str, Any]) -> None:
    """Write config override for hot-reload by the runner."""
    override_dir = Path(data_dir) / "wolf"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_path = override_dir / "config-override.json"
    with open(override_path, "w") as f:
        json.dump(config, f, indent=2)


# CLI entry point: python3 -m cli.api.status_reader [command] [--data-dir DIR] [--limit N]
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["status", "strategies", "trades", "reflect", "scanner", "journal"],
        default="status",
        nargs="?",
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    if args.command == "strategies":
        print(json.dumps(read_strategies(), indent=2))
    elif args.command == "trades":
        print(json.dumps(read_trades(args.data_dir, limit=args.limit), indent=2))
    elif args.command == "reflect":
        print(json.dumps(read_reflect(args.data_dir), indent=2))
    elif args.command == "scanner":
        print(json.dumps(read_scanner(args.data_dir), indent=2))
    elif args.command == "journal":
        print(json.dumps(read_journal(args.data_dir, limit=args.limit), indent=2))
    else:
        print(json.dumps(read_status(args.data_dir), indent=2))
