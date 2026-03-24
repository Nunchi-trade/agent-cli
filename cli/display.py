"""Console display formatting using ANSI escape codes."""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
WHITE = "\033[37m"


def _pnl_color(val: float) -> str:
    if val > 0:
        return GREEN
    elif val < 0:
        return RED
    return DIM


def _sign(val: float) -> str:
    return f"+{val}" if val >= 0 else str(val)


def tick_line(
    tick: int,
    instrument: str,
    mid: float,
    pos_qty: float,
    avg_entry: float,
    upnl: float,
    rpnl: float,
    orders_sent: int,
    orders_filled: int,
    risk_ok: bool,
    reduce_only: bool = False,
) -> str:
    """One-line tick summary for console output."""
    ts = time.strftime("%H:%M:%S")
    coin = instrument.replace("-PERP", "").replace("-USDYP", "")

    pos_str = f"{_sign(pos_qty)}" if pos_qty != 0 else "flat"
    entry_str = f" @ {avg_entry:.2f}" if pos_qty != 0 else ""

    risk_str = f"{GREEN}OK{RESET}"
    if not risk_ok:
        risk_str = f"{RED}BLOCKED{RESET}"
    elif reduce_only:
        risk_str = f"{YELLOW}REDUCE{RESET}"

    upnl_c = _pnl_color(upnl)
    rpnl_c = _pnl_color(rpnl)

    return (
        f"{DIM}[{ts}]{RESET} {BOLD}T{tick}{RESET} "
        f"{CYAN}{coin}{RESET} mid={mid:.4f} | "
        f"pos={pos_str}{entry_str} | "
        f"uPnL={upnl_c}{_sign(round(upnl, 2))}{RESET} "
        f"rPnL={rpnl_c}{_sign(round(rpnl, 2))}{RESET} | "
        f"{orders_sent} sent {orders_filled} filled | "
        f"risk: {risk_str}"
    )


def status_table(
    strategy: str,
    instrument: str,
    network: str,
    tick_count: int,
    start_time_ms: int,
    pos_qty: float,
    avg_entry: float,
    notional: float,
    upnl: float,
    rpnl: float,
    drawdown_pct: float,
    reduce_only: bool,
    safe_mode: bool,
    total_orders: int,
    total_fills: int,
    recent_fills: List[Dict[str, Any]],
) -> str:
    """Full status display for `hl status`."""
    now = time.time()
    elapsed_s = (now - start_time_ms / 1000) if start_time_ms > 0 else 0
    elapsed_min = int(elapsed_s // 60)

    total_pnl = upnl + rpnl
    upnl_c = _pnl_color(upnl)
    rpnl_c = _pnl_color(rpnl)
    total_c = _pnl_color(total_pnl)

    lines = [
        f"{BOLD}=== HL Autonomous Trader ==={RESET}",
        f"Strategy: {CYAN}{strategy}{RESET} | Instrument: {CYAN}{instrument}{RESET} | Network: {network}",
        f"Ticks: {tick_count} | Uptime: {elapsed_min}min | Orders: {total_orders} placed, {total_fills} filled",
        "",
        f"{BOLD}Position:{RESET}  {_sign(pos_qty)} @ ${avg_entry:.4f} avg",
        f"{BOLD}Notional:{RESET}  ${notional:.2f}",
        f"{BOLD}PnL:{RESET}      Unrealized: {upnl_c}${_sign(round(upnl, 2))}{RESET} | "
        f"Realized: {rpnl_c}${_sign(round(rpnl, 2))}{RESET} | "
        f"Total: {total_c}${_sign(round(total_pnl, 2))}{RESET}",
        f"{BOLD}Drawdown:{RESET} {drawdown_pct:.2f}%",
        "",
        f"{BOLD}Risk State:{RESET}",
        f"  Reduce-only: {'YES' if reduce_only else 'NO'} | "
        f"Safe mode: {'YES' if safe_mode else 'NO'}",
    ]

    if recent_fills:
        lines.append("")
        lines.append(f"{BOLD}Recent Fills:{RESET}")
        for f in recent_fills[-5:]:
            side_c = GREEN if f.get("side") == "buy" else RED
            lines.append(
                f"  {f.get('timestamp', '')}  {side_c}{f.get('side', '').upper()}{RESET}  "
                f"{f.get('quantity', '')} @ ${f.get('price', '')}"
            )

    return "\n".join(lines)


def strategy_table(registry: Dict[str, Dict[str, Any]]) -> str:
    """Format strategy registry for `hl strategies`."""
    lines = [
        f"{BOLD}Available Strategies{RESET}",
        f"{'Name':<20} {'Description':<55} {'Default Params'}",
        f"{'-'*20} {'-'*55} {'-'*30}",
    ]
    for name, info in sorted(registry.items()):
        params = ", ".join(f"{k}={v}" for k, v in info.get("params", {}).items())
        lines.append(f"{CYAN}{name:<20}{RESET} {info['description']:<55} {DIM}{params}{RESET}")
    return "\n".join(lines)


def account_table(state: Dict[str, Any]) -> str:
    """Format account state for `hl account`."""
    perp_value = state.get("account_value", 0)
    spot_usdc = state.get("spot_usdc", 0)
    total_value = perp_value + spot_usdc

    lines = [
        f"{BOLD}=== HL Account ==={RESET}",
        f"Address:      {state.get('address', 'N/A')}",
        f"Total Value:  ${total_value:.2f}",
        f"  Perps:      ${perp_value:.2f}",
    ]
    if spot_usdc:
        lines.append(f"  Spot USDC:  ${spot_usdc:.2f}")
    spot_balances = state.get("spot_balances", [])
    for b in spot_balances:
        if b["coin"] != "USDC" and float(b["total"]) != 0:
            lines.append(f"  Spot {b['coin']:6s} {float(b['total']):.4f}")
    lines.extend([
        f"Margin Used:  ${state.get('total_margin', 0):.2f}",
        f"Withdrawable: ${state.get('withdrawable', 0):.2f}",
    ])
    return "\n".join(lines)


def shutdown_summary(
    tick_count: int,
    total_placed: int,
    total_filled: int,
    total_pnl: float,
    elapsed_s: float,
) -> str:
    """Print summary on graceful shutdown."""
    pnl_c = _pnl_color(total_pnl)
    return (
        f"\n{BOLD}=== Shutdown Summary ==={RESET}\n"
        f"Ticks:   {tick_count}\n"
        f"Orders:  {total_placed} placed, {total_filled} filled\n"
        f"PnL:     {pnl_c}${_sign(round(total_pnl, 2))}{RESET}\n"
        f"Runtime: {int(elapsed_s)}s"
    )
