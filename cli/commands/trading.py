"""hl trading — joined data surfaces for UIs / bridges.

`hl trading summary` emits ONE JSON object that joins everything a Trading
Panel needs, so UIs and bridges consume a stable contract instead of scraping
human tables:

  * account      — live HL account state (own key, or read-only via --address)
  * pnl          — realized (FIFO round-trips) + unrealized P&L from trades.jsonl
  * fills        — recent raw fills from <data-dir>/trades.jsonl
  * journal      — recent structured journal rows
  * strategy     — current persisted engine/strategy state (StateDB)
  * registry     — available strategies + YEX markets

Every section degrades gracefully: a missing data-dir or trades.jsonl yields
zeros / empty lists rather than a crash.
"""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

trading_app = typer.Typer(
    name="trading",
    help="Trading data surfaces — joined JSON contracts for UIs and bridges.",
    no_args_is_help=True,
)


def _read_trades(trades_path: Path) -> List[Dict[str, Any]]:
    """Read all trade records from a JSONL file. Empty list if absent/corrupt."""
    if not trades_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(trades_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _compute_pnl(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Realized P&L (FIFO round-trips) + fees/volume from raw trade rows.

    Reuses the REFLECT engine so accounting matches `hl reflect`. Unrealized
    P&L is NOT derivable from the trade log alone (needs a live mark price);
    callers should read it from the `account`/`strategy` sections instead.
    """
    from modules.reflect_engine import ReflectEngine, TradeRecord

    if not trades:
        return {
            "realized_pnl": 0.0,
            "gross_pnl": 0.0,
            "total_fees": 0.0,
            "round_trips": 0,
            "win_rate": 0.0,
            "open_position_legs": 0,
        }
    records = [TradeRecord.from_dict(t) for t in trades]
    m = ReflectEngine().compute(records)
    return {
        "realized_pnl": round(m.net_pnl, 6),
        "gross_pnl": round(m.gross_pnl, 6),
        "total_fees": round(m.total_fees, 6),
        "round_trips": m.total_round_trips,
        "win_rate": round(m.win_rate, 2),
        "open_position_legs": m.orphan_trade_count,
    }


def _read_journal(journal_path: Path, limit: int) -> Dict[str, Any]:
    """Recent journal rows (newest-first). Empty if absent/corrupt."""
    if not journal_path.exists():
        return {"entries": [], "total": 0}
    rows: List[Dict[str, Any]] = []
    try:
        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
    except OSError:
        return {"entries": [], "total": 0}
    total = len(rows)
    rows.reverse()
    return {"entries": rows[:limit], "total": total}


def _read_strategy_state(data_dir: Path) -> Dict[str, Any]:
    """Current persisted engine/strategy state from StateDB. Empty if absent."""
    db_path = data_dir / "state.db"
    if not db_path.exists():
        return {}
    try:
        from parent.store import StateDB

        db = StateDB(path=str(db_path))
        try:
            positions_data = db.get("positions") or {}
            pos_qty = 0.0
            upnl = 0.0
            rpnl = 0.0
            for _agent_id, instruments in positions_data.get("agents", {}).items():
                for _inst, pos in instruments.items():
                    pos_qty = float(pos.get("net_qty", "0"))
                    rpnl = float(pos.get("realized_pnl", "0"))
                    if "unrealized_pnl" in pos:
                        upnl = float(pos["unrealized_pnl"])
            order_stats = db.get("order_stats") or {}
            return {
                "strategy_id": db.get("strategy_id") or "unknown",
                "instrument": db.get("instrument") or "unknown",
                "tick_count": db.get("tick_count") or 0,
                "start_time_ms": db.get("start_time_ms") or 0,
                "position_qty": pos_qty,
                "unrealized_pnl": upnl,
                "realized_pnl": rpnl,
                "total_orders": order_stats.get("total_placed", 0),
                "total_fills": order_stats.get("total_filled", 0),
            }
        finally:
            db.close()
    except Exception:
        return {}


def _read_registry() -> Dict[str, Any]:
    """Available strategies + YEX markets from the static registry."""
    try:
        from cli.strategy_registry import STRATEGY_REGISTRY, YEX_MARKETS

        return {
            "strategies": {
                name: {"description": info["description"], "params": info["params"]}
                for name, info in STRATEGY_REGISTRY.items()
            },
            "markets": {name: info["description"] for name, info in YEX_MARKETS.items()},
        }
    except Exception:
        return {"strategies": {}, "markets": {}}


def _fetch_account(
    ro_address: Optional[str], mainnet: bool
) -> Dict[str, Any]:
    """Live account state: read-only by address, else via own key. {} on fail."""
    from cli.hl_adapter import read_only_account_state

    if ro_address:
        try:
            return read_only_account_state(ro_address, testnet=not mainnet) or {}
        except Exception as e:
            return {"error": str(e), "address": ro_address}

    # Authenticated path — only if a key is resolvable; never crash the contract.
    try:
        from cli.config import TradingConfig
        from cli.hl_adapter import DirectHLProxy
        from parent.hl_proxy import HLProxy

        key = TradingConfig().get_private_key()
        hl = DirectHLProxy(HLProxy(private_key=key, testnet=not mainnet))
        return hl.get_account_state() or {}
    except Exception as e:
        return {"error": str(e)}


@trading_app.command("summary")
def trading_summary(
    workspace: Optional[str] = typer.Option(
        None, "--workspace", help="Workspace id (echoed back into the JSON for the caller)"
    ),
    agent: Optional[str] = typer.Option(
        None, "--agent", help="Agent id (echoed back into the JSON for the caller)"
    ),
    data_dir: str = typer.Option(
        "data/cli", "--data-dir", help="Directory holding trades.jsonl / state.db / journal.jsonl"
    ),
    address: Optional[str] = typer.Option(
        None, "--address", "-a",
        help="Read-only: fetch account for this 0x address from HL public API "
             "(no key). Falls back to HL_VIEW_AS_USER.",
    ),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max recent fills / journal rows"),
    no_account: bool = typer.Option(
        False, "--no-account",
        help="Skip the live account fetch (offline / file-only summary).",
    ),
    json_out: bool = typer.Option(
        True, "--json/--no-json",
        help="Emit JSON (default). --no-json is reserved; JSON is the contract.",
    ),
):
    """Emit one joined JSON object describing a trading agent's full state."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.view_mode import view_address

    ddir = Path(data_dir)
    ro_address = view_address(address)

    trades = _read_trades(ddir / "trades.jsonl")
    fills = list(reversed(trades))[:limit]  # newest-first

    account: Dict[str, Any] = {}
    if not no_account:
        account = _fetch_account(ro_address, mainnet)

    summary: Dict[str, Any] = {
        "workspace": workspace,
        "agent": agent,
        "data_dir": str(ddir),
        "network": "mainnet" if mainnet else "testnet",
        "view_only": ro_address is not None,
        "address": ro_address or account.get("address"),
        "account": account,
        "pnl": _compute_pnl(trades),
        "fills": fills,
        "fills_total": len(trades),
        "journal": _read_journal(ddir / "journal.jsonl", limit),
        "strategy": _read_strategy_state(ddir),
        "registry": _read_registry(),
    }

    # json_out defaults True and is the contract; the flag exists so a future
    # human renderer can hang off --no-json without breaking callers.
    typer.echo(_json.dumps(summary, default=str))
