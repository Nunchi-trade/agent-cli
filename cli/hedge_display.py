"""Display helpers for the `hedge` and `margin` sub-apps.

Ported from nunchi-cli `cli/display.py` (the hedge/margin blocks). Kept in
a dedicated module so agent-cli's existing `cli/display.py` is untouched.
Color constants are reused from `cli.display` so the look matches the rest
of the CLI.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from cli.display import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW


def margin_status_block(state: Dict[str, Any], isolated: list, *, mainnet: bool = False) -> str:
    """Format the cross + isolated margin breakdown for `hl margin status`."""
    from execution.margin_math import (
        free_collateral_usd,
        maintenance_margin_ratio,
        margin_utilization,
        format_pct,
        format_usd,
    )

    account_value = float(state.get("account_value", 0) or 0)
    total_margin = float(state.get("total_margin", 0) or 0)
    withdrawable = float(state.get("withdrawable", 0) or 0)
    spot_usdc = float(state.get("spot_usdc", 0) or 0)

    free = free_collateral_usd(account_value, total_margin)
    util = margin_utilization(total_margin, account_value)
    # HL doesn't return cross maintenance margin on the merged surface; show 0
    # for now and let `hl account` carry the per-dex deep dive if needed.
    maint = maintenance_margin_ratio(0.0, account_value)
    network = "mainnet" if mainnet else "testnet"
    util_color = RED if util > 0.7 else (YELLOW if util > 0.5 else GREEN)

    lines = [
        f"{BOLD}=== Margin ({network}) ==={RESET}",
        f"Address:           {state.get('address', 'N/A')}",
        f"Account value:     {format_usd(account_value)}",
        f"  Spot USDC:       {format_usd(spot_usdc)}" if spot_usdc else None,
        f"Margin used:       {format_usd(total_margin)}",
        f"Free collateral:   {GREEN}{format_usd(free)}{RESET}",
        f"Withdrawable:      {format_usd(withdrawable)}",
        f"Utilization:       {util_color}{format_pct(util)}{RESET}",
        f"Maint. ratio:      {format_pct(maint)}",
    ]
    lines = [l for l in lines if l is not None]

    if isolated:
        lines.append("")
        lines.append(f"{BOLD}Isolated positions ({len(isolated)}):{RESET}")
        lines.append(
            f"  {'Coin':<14} {'Side':<5} {'Size':>10} {'Notional':>14} {'Margin':>12} {'Lev':>4}"
        )
        lines.append("  " + "-" * 70)
        for pos in isolated:
            coin = pos.get("coin", "?")
            szi = float(pos.get("szi", "0") or 0)
            side = "LONG" if szi >= 0 else "SHORT"
            size = abs(szi)
            notional = float(pos.get("positionValue", 0) or 0)
            margin_used = float(pos.get("marginUsed", 0) or 0)
            lev = (pos.get("leverage") or {}).get("value", 1)
            side_color = GREEN if side == "LONG" else RED
            lines.append(
                f"  {CYAN}{coin:<14}{RESET} {side_color}{side:<5}{RESET} "
                f"{size:>10.4f} {format_usd(notional):>14} {format_usd(margin_used):>12} {lev:>4}x"
            )

    lines.extend([
        "",
        f"{DIM}Use `hl margin deposit AMOUNT` or `hl margin isolated COIN AMOUNT` "
        f"to adjust. `hl margin dexes` lists HIP-3 sub-DEXes.{RESET}",
    ])
    return "\n".join(lines)


def hedge_proposal_block(proposal, snapshot, *, mainnet: bool = False) -> str:
    """Format a CFIHedgeProposal for `hl hedge propose` / `execute`.

    Pattern matches `account_table` — multi-line ANSI string with bold
    headers, coloured rates, and a horizon table.
    """
    from strategies.cfi_hedge import (
        format_apy_pct,
        format_usd,
        format_usd_signed,
        k2_time_constant_hours,
    )

    pos = proposal.source_position
    profile = proposal.profile
    k_apy_color = GREEN if proposal.k_fixed_apy <= 0 else YELLOW
    cur_apy_color = RED if proposal.current_funding_apy > 0 else GREEN
    excess_color = GREEN if proposal.excess_apy > 0 else RED if proposal.excess_apy < 0 else DIM
    drift_arrow = (
        "↑" if proposal.wire_drift_per_hour_usd > 0.5
        else "↓" if proposal.wire_drift_per_hour_usd < -0.5
        else "↔"
    )
    src = "SEDA live" if snapshot.source == "seda" else f"{YELLOW}replay (HL fundingHistory){RESET}"
    network = "mainnet" if mainnet else "testnet"

    lines = [
        f"{BOLD}=== Hedge {pos.coin} (CFI v2) ==={RESET}",
        f"Network: {network} | Profile: L={profile.vol_mult_l}, β={profile.k2_beta:.6f} "
        f"(τ ≈ {k2_time_constant_hours(profile):.1f}h EMA)",
        f"K2 source: {src}",
        "",
        f"{BOLD}Detected on Hyperliquid:{RESET}",
        f"  Coin:        {CYAN}{pos.coin}{RESET}",
        f"  Side:        {(GREEN if pos.side == 'long' else RED)}{pos.side.upper()}{RESET}",
        f"  Size:        {pos.size_coin:.4f} {pos.coin}",
        f"  Notional:    {format_usd(pos.notional_usd)}",
        f"  Entry/Mark:  {format_usd(pos.entry_px)} → {format_usd(pos.mark_px)}",
        f"  Funding (ann.): {cur_apy_color}{format_apy_pct(proposal.current_funding_apy)}{RESET}",
        "",
        f"{BOLD}Rate panel:{RESET}",
        f"  K2 locked:    {k_apy_color}{format_apy_pct(proposal.k_fixed_apy)}{RESET}",
        f"  Floating now: {cur_apy_color}{format_apy_pct(proposal.current_funding_apy)}{RESET}",
        f"  Wire drift:   {excess_color}{drift_arrow} "
        f"{format_usd_signed(proposal.wire_drift_per_hour_usd)}/hr{RESET} "
        f"({excess_color}{format_apy_pct(proposal.excess_apy)} excess{RESET})",
    ]
    if snapshot.oracle_px is not None:
        lines.append(f"  Wire price:   ${snapshot.oracle_px:,.2f}")

    existing, hedge = proposal.legs
    lines.extend([
        "",
        f"{BOLD}Proposed hedge:{RESET}",
        f"  Ratio:               {CYAN}1/{profile.vol_mult_l}{RESET} "
        f"(capital efficiency {proposal.capital_efficiency * 100:.1f}%)",
        f"  Perp notional:       {format_usd(pos.notional_usd)}",
        f"  CFI v2 hedge:        {CYAN}{format_usd(proposal.hedge_notional_usd)}{RESET} "
        f"({hedge.market}, {hedge.side})",
        f"  Total capital req:   {format_usd(proposal.total_capital_usd)}",
        "",
        f"{BOLD}Funding cost (floating vs K2-locked):{RESET}",
        f"  {'Horizon':<12} {'Floating':>16} {'K2-locked':>16} {'Δ':>16}",
        f"  {'-'*12} {'-'*16} {'-'*16} {'-'*16}",
    ])
    for h in proposal.projections:
        unhedged_str = format_usd_signed(-h.unhedged_usd, 2)
        hedged_str = format_usd_signed(-h.hedged_usd, 2)
        delta_color = GREEN if h.savings_usd >= 0 else RED
        delta_str = f"{delta_color}{format_usd_signed(h.savings_usd, 2)}{RESET}"
        lines.append(f"  {h.label:<12} {RED}{unhedged_str}{RESET:>10} {hedged_str:>16} {delta_str:>30}")

    lines.extend([
        "",
        f"{DIM}Execution: signs against {hedge.market} (HL HIP-3, asset index resolved "
        f"by the HL Python SDK). 10 bps Nunchi builder fee. No hermes-api dep.{RESET}",
    ])
    return "\n".join(lines)


def hedge_status_block(live: list, *, mainnet: bool = False) -> str:
    """Format active hedge jobs + live drift for `hl hedge status`."""
    from strategies.cfi_hedge import (
        format_apy_pct,
        format_duration,
        format_usd,
        format_usd_signed,
    )

    network = "mainnet" if mainnet else "testnet"
    lines = [
        f"{BOLD}=== Active hedges ({network}) ==={RESET}",
        f"{'ID':<24} {'Coin':<6} {'Notional':<12} {'Locked':>10} {'Floating':>10} {'Drift':>10} {'Saved':>12} {'Uptime':>10}",
        "-" * 110,
    ]
    now_ms = int(time.time() * 1000)
    for entry in live:
        j = entry["job"]
        locked = j.get("locked_k2_apy", 0.0)
        current = entry.get("current_funding_apy")
        drift = entry.get("drift_apy", 0.0)
        savings = entry.get("savings_usd", 0.0)
        submitted_ms = int(j.get("submitted_at_ms", now_ms))
        uptime = format_duration(now_ms - submitted_ms)

        drift_color = GREEN if drift > 0.0005 else RED if drift < -0.0005 else DIM
        savings_color = GREEN if savings >= 0 else RED
        current_str = format_apy_pct(current) if current is not None else "  —"
        lines.append(
            f"{j.get('id', '?'):<24} "
            f"{CYAN}{j.get('coin', '?'):<6}{RESET} "
            f"{format_usd(j.get('perp_notional_usd', 0)):<12} "
            f"{format_apy_pct(locked):>10} "
            f"{current_str:>10} "
            f"{drift_color}{format_apy_pct(drift):>10}{RESET} "
            f"{savings_color}{format_usd_signed(savings, 2):>12}{RESET} "
            f"{uptime:>10}"
        )

    lines.append("")
    lines.append(f"{DIM}State persisted at ~/.nunchi/hedges.json. Drift = floating − locked K2.{RESET}")
    return "\n".join(lines)
