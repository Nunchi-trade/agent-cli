"""hl status — show positions, PnL, risk state."""
from __future__ import annotations

import json as _json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import typer


def status_cmd(
    data_dir: str = typer.Option(
        "data/cli", "--data-dir",
        help="Directory where state is persisted",
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w",
        help="Continuously refresh",
    ),
    interval: float = typer.Option(
        5.0, "--interval",
        help="Refresh interval when watching (seconds)",
    ),
    address: Optional[str] = typer.Option(
        None, "--address", "-a",
        help="Read-only: attach live account state for this 0x address from "
             "HL public API (no key). Falls back to HL_VIEW_AS_USER env var.",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="Emit machine-readable JSON instead of a human table.",
    ),
):
    """Show positions, PnL, and risk state from persisted state.

    With --address (or HL_VIEW_AS_USER) the JSON output additionally carries a
    live `account` block fetched read-only from the public info API.
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from parent.store import StateDB, JSONLStore
    from parent.position_tracker import PositionTracker
    from parent.risk_manager import RiskManager
    from cli.display import status_table
    from cli.view_mode import view_address

    db = StateDB(path=f"{data_dir}/state.db")
    trades = JSONLStore(path=f"{data_dir}/trades.jsonl")

    ro_address = view_address(address)

    def _collect() -> Dict[str, Any]:
        """Gather the status fields into a plain dict (state-source of truth)."""
        tick_count = db.get("tick_count") or 0
        start_time_ms = db.get("start_time_ms") or 0
        strategy_id = db.get("strategy_id") or "unknown"
        instrument = db.get("instrument") or "unknown"
        order_stats = db.get("order_stats") or {}

        positions_data = db.get("positions")
        risk_data = db.get("risk")

        pos_qty = 0.0
        avg_entry = 0.0
        notional = 0.0
        upnl = 0.0
        rpnl = 0.0

        if positions_data is not None:
            agent_positions = positions_data.get("agents", {})
            for _agent_id, instruments in agent_positions.items():
                for _inst, pos_data in instruments.items():
                    pos_qty = float(pos_data.get("net_qty", "0"))
                    avg_entry = float(pos_data.get("avg_entry_price", "0"))
                    notional = float(pos_data.get("notional", "0"))
                    rpnl = float(pos_data.get("realized_pnl", "0"))
                    if "unrealized_pnl" in pos_data:
                        upnl = float(pos_data["unrealized_pnl"])

        dd_pct = 0.0
        reduce_only = False
        safe_mode = False
        if risk_data:
            rm = RiskManager.from_dict(risk_data)
            reduce_only = rm.state.reduce_only
            safe_mode = rm.state.safe_mode
            if risk_data.get("state"):
                rs = risk_data["state"]
                tvl = float(risk_data.get("limits", {}).get("tvl", "100000"))
                dd = float(rs.get("daily_drawdown", "0"))
                dd_pct = (dd / tvl * 100) if tvl > 0 else 0.0

        recent = trades.read_all()[-5:] if trades.path.exists() else []

        return {
            "found": positions_data is not None,
            "strategy": strategy_id,
            "instrument": instrument,
            "network": "testnet",
            "tick_count": tick_count,
            "start_time_ms": start_time_ms,
            "position_qty": pos_qty,
            "avg_entry": avg_entry,
            "notional": notional,
            "unrealized_pnl": upnl,
            "realized_pnl": rpnl,
            "total_pnl": upnl + rpnl,
            "drawdown_pct": round(dd_pct, 4),
            "reduce_only": reduce_only,
            "safe_mode": safe_mode,
            "total_orders": order_stats.get("total_placed", 0),
            "total_fills": order_stats.get("total_filled", 0),
            "recent_fills": recent,
        }

    def _render_table(s: Dict[str, Any]) -> None:
        if not s["found"]:
            typer.echo("No state found. Is the engine running?")
            return
        output = status_table(
            strategy=s["strategy"],
            instrument=s["instrument"],
            network=s["network"],
            tick_count=s["tick_count"],
            start_time_ms=s["start_time_ms"],
            pos_qty=s["position_qty"],
            avg_entry=s["avg_entry"],
            notional=s["notional"],
            upnl=s["unrealized_pnl"],
            rpnl=s["realized_pnl"],
            drawdown_pct=s["drawdown_pct"],
            reduce_only=s["reduce_only"],
            safe_mode=s["safe_mode"],
            total_orders=s["total_orders"],
            total_fills=s["total_fills"],
            recent_fills=s["recent_fills"],
        )
        if watch:
            print("\033[2J\033[H", end="")  # Clear screen
        print(output)

    def _render_json(s: Dict[str, Any]) -> None:
        s = dict(s)
        s["view_only"] = ro_address is not None
        # Attach a live account block when a read-only address is in effect.
        if ro_address:
            try:
                from cli.hl_adapter import read_only_account_state
                s["account"] = read_only_account_state(ro_address, testnet=True)
                s["account_address"] = ro_address
            except Exception as e:  # never crash the JSON contract
                s["account"] = None
                s["account_error"] = str(e)
        if watch:
            print("\033[2J\033[H", end="")
        print(_json.dumps(s, default=str))

    render = _render_json if json_out else _render_table

    if watch:
        try:
            while True:
                render(_collect())
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
    else:
        render(_collect())

    db.close()
