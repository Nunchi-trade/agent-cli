"""Maps short strategy names to module:class paths."""
from __future__ import annotations

from typing import Any, Dict

STRATEGY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "simple_mm": {
        "path": "strategies.simple_mm:SimpleMMStrategy",
        "description": "Symmetric bid/ask quoting around mid price",
        "params": {"spread_bps": 10.0, "size": 1.0},
    },
    "avellaneda_mm": {
        "path": "strategies.avellaneda_mm:AvellanedaStoikovMM",
        "description": "Inventory-aware market maker (Avellaneda-Stoikov model)",
        "params": {"gamma": 0.1, "k": 1.5, "base_size": 1.0},
    },
    "mean_reversion": {
        "path": "strategies.mean_reversion:MeanReversionStrategy",
        "description": "Trade when price deviates from SMA",
        "params": {"window": 20, "threshold_bps": 30.0, "size": 1.0},
    },
    "hedge_agent": {
        "path": "strategies.hedge_agent:HedgeAgent",
        "description": "inventory reducer (delta control)",
        "params": {"inventory_threshold": 3.0},
    },
    "cfi_hedge": {
        "path": "strategies.cfi_hedge_agent:CfiHedgeAgent",
        "description": "CFI-v2 funding-cost hedge",
        "params": {
            "notional_trigger": 100000.0,
            "max_hedge_notional": 50000.0,
            "max_per_day": 5,
            "min_interval_seconds": 300,
        },
    },
    "rfq_agent": {
        "path": "strategies.rfq_agent:RFQAgent",
        "description": "Block-size liquidity for dark RFQ flow",
        "params": {"min_size": 0.5, "spread_bps": 15.0},
    },
    "aggressive_taker": {
        "path": "strategies.aggressive_taker:AggressiveTaker",
        "description": "Crosses the spread with directional bias",
        "params": {"size": 2.0, "bias_amplitude": 0.35},
    },
    "claude_agent": {
        "path": "strategies.claude_agent:ClaudeStrategy",
        "description": "LLM trading agent — Gemini (default), Claude, OpenAI, or ClawRouter (x402 USDC)",
        "params": {"model": "gemini-2.0-flash", "base_size": 0.5},
    },
    "engine_mm": {
        "path": "strategies.engine_mm:EngineMMStrategy",
        "description": "Production quoting engine MM — composite FV, dynamic spreads, multi-level ladder",
        "params": {"base_size": 1.0, "num_levels": 3},
    },
    "funding_arb": {
        "path": "strategies.funding_arb:FundingArbStrategy",
        "description": "HL funding-rate bias MM — cross-venue arb not wired; HL-only",
        "params": {"divergence_threshold_bps": 2.0, "max_bias_bps": 5.0},
    },
    "regime_mm": {
        "path": "strategies.regime_mm:RegimeMMStrategy",
        "description": "Vol-regime adaptive MM — switches behavior by volatility regime",
        "params": {"base_size": 1.0},
    },
    "liquidation_mm": {
        "path": "strategies.liquidation_mm:LiquidationMMStrategy",
        "description": "Liquidation flow MM — provides liquidity during cascade events",
        "params": {"oi_drop_threshold_pct": 5.0, "cascade_spread_mult": 2.5},
    },
    "momentum_breakout": {
        "path": "strategies.momentum_breakout:MomentumBreakoutStrategy",
        "description": "Momentum breakout — enter on volume + price breakout above/below N-period range",
        "params": {"lookback": 20, "breakout_threshold_bps": 50.0, "size": 1.0},
    },
    "grid_mm": {
        "path": "strategies.grid_mm:GridMMStrategy",
        "description": "Grid market maker — fixed-interval levels above and below mid",
        "params": {"grid_spacing_bps": 10.0, "num_levels": 5, "size_per_level": 0.5},
    },
    "basis_arb": {
        "path": "strategies.basis_arb:BasisArbStrategy",
        "description": "Basis arbitrage — trades implied basis from funding rate",
        "params": {"basis_threshold_bps": 5.0, "size": 1.0},
    },
    "simplified_ensemble": {
        "path": "strategies.simplified_ensemble:SimplifiedEnsembleStrategy",
        "description": "6-signal ensemble (4/6 vote) — ported from auto-research exp52 (score 13.5)",
        "params": {"size": 1.0},
    },
    "funding_momentum": {
        "path": "strategies.funding_momentum:FundingMomentumStrategy",
        "description": "Funding rate mean-reversion — trade extreme funding z-scores with EMA confirmation",
        "params": {"size": 1.0},
    },
    "oi_divergence": {
        "path": "strategies.oi_divergence:OIDivergenceStrategy",
        "description": "OI divergence filter — enter on price/OI agreement, exit on divergence",
        "params": {"size": 1.0},
    },
    "trend_follower": {
        "path": "strategies.trend_follower:TrendFollowerStrategy",
        "description": "EMA crossover + ADX trend strength filter — avoid chop, catch sustained moves",
        "params": {"size": 1.0},
    },
}

# YEX market definitions — Nunchi HIP-3 yield perpetuals (testnet)
YEX_MARKETS: Dict[str, Dict[str, str]] = {
    "VXX-USDYP": {
        "hl_coin": "yex:VXX",
        "description": "Volatility index (VXX) yield perpetual",
    },
    "US3M-USDYP": {
        "hl_coin": "yex:US3M",
        "description": "US 3-month Treasury rate yield perpetual",
    },
    "BTCSWP-USDYP": {
        "hl_coin": "yex:BTCSWP",
        "description": "BTC interest rate swap yield perpetual (YEX testnet)",
    },
}


# OSRS swap perp (testnet HIP-3 dex, explicit instrument)
SWAP_MARKETS: Dict[str, Dict[str, str]] = {
    "BTCSWP-OSRS": {
        "hl_coin": "osrs:BTCSWP",
        "description": "BTC interest rate swap perp (OSRS testnet)",
    },
}

# Paragon mainnet HIP-3 markets
PARA_MARKETS: Dict[str, Dict[str, str]] = {
    "BTCSWP-PARA": {
        "hl_coin": "para:BTCSWP",
        "description": "BTC interest rate swap yield perpetual (Paragon mainnet)",
    },
}


def resolve_strategy_path(name_or_path: str) -> str:
    """Resolve a short name to a full module:class path.

    Accepts either a short name ('avellaneda_mm') or
    a full path ('strategies.avellaneda_mm:AvellanedaStoikovMM').
    """
    if ":" in name_or_path:
        return name_or_path
    entry = STRATEGY_REGISTRY.get(name_or_path)
    if entry is None:
        available = ", ".join(sorted(STRATEGY_REGISTRY.keys()))
        raise ValueError(f"Unknown strategy '{name_or_path}'. Available: {available}")
    return entry["path"]


def resolve_instrument(name: str, mainnet: bool | None = None) -> str:
    """Resolve an instrument name to the canonical form."""
    from common.models import BTCSWP_ASSET, coin_to_instrument, is_mainnet

    normalized = name.strip()
    lower = normalized.lower()

    for markets in (PARA_MARKETS, SWAP_MARKETS, YEX_MARKETS):
        for name_key, info in markets.items():
            if lower == info["hl_coin"].lower():
                return name_key

    if lower.startswith("yex:") or lower.startswith("para:") or lower.startswith("osrs:"):
        return coin_to_instrument(normalized, mainnet=mainnet)

    if lower == BTCSWP_ASSET.lower():
        return "BTCSWP-PARA" if is_mainnet(mainnet) else "BTCSWP-USDYP"

    return normalized
