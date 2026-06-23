"""Pure margin helpers for the `nunchi margin` sub-app.

Decimal-USD in, decimal-USD out. Mirrors the math in
`~/demo-ide/src/lib/margin-math.ts` so the CLI and the tile show the
same utilization / free-collateral numbers for a given account snapshot.
"""
from __future__ import annotations

import math

USDC_DECIMALS = 6


def free_collateral_usd(account_value_usd: float, total_margin_used_usd: float) -> float:
    """Free cross-account collateral = max(0, accountValue - totalMarginUsed)."""
    return max(0.0, account_value_usd - total_margin_used_usd)


def margin_utilization(total_margin_used_usd: float, account_value_usd: float) -> float:
    """Cross-account margin usage as a fraction in [0, 1+]."""
    if account_value_usd <= 0:
        return 0.0
    return total_margin_used_usd / account_value_usd


def maintenance_margin_ratio(
    maintenance_margin_used_usd: float, account_value_usd: float
) -> float:
    """Cross-account maintenance margin ratio (1 = liquidation threshold)."""
    if account_value_usd <= 0:
        return 0.0
    return maintenance_margin_used_usd / account_value_usd


def decimal_usd_to_raw_usdc(amount_usd: float) -> int:
    """Decimal USD ('50.00') → 6-decimal raw USDC integer (50_000_000).

    Sign preserved. Rounds toward zero so a 50.000001 USD input never
    under-deposits the full requested amount. Raises on non-finite.
    """
    if not math.isfinite(amount_usd):
        raise ValueError(f"decimal_usd_to_raw_usdc: non-finite amount {amount_usd}")
    scaled = math.trunc(amount_usd * (10 ** USDC_DECIMALS))
    return int(scaled)


def format_pct(fraction: float, digits: int = 2) -> str:
    return f"{fraction * 100:.{digits}f}%"


def format_usd(amount: float, digits: int = 2) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.{digits}f}"
