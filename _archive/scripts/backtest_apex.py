#!/usr/bin/env python3
"""Backtest harness — replays trades through ApexConfig filters.

Usage:
    python3 scripts/backtest_apex.py --config apex_config.json [--trades data/cli/trades.jsonl]

Outputs metrics in autoresearch-parseable key: value format.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure project root is on sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from modules.apex_config import ApexConfig
from modules.reflect_engine import ReflectEngine, TradeRecord


def load_trades(path: str) -> list[TradeRecord]:
    """Load trades from a JSONL file (one JSON object per line)."""
    trades: list[TradeRecord] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            trades.append(TradeRecord.from_dict(json.loads(line)))
    return trades


def replay_with_config(
    trades: list[TradeRecord],
    config: ApexConfig,
) -> list[TradeRecord]:
    """Filter trades that would have passed ApexConfig thresholds.

    Filtering rules:
    - Skip entries where radar_score (from meta JSON) < config.radar_score_threshold
    - Skip entries where pulse_confidence (from meta JSON) < config.pulse_confidence_threshold
    - Apply daily_loss_limit gating: stop accepting entries once cumulative
      realized loss in a calendar day exceeds the limit
    - Apply max_same_direction filtering: skip if direction queue is full
    """
    filtered: list[TradeRecord] = []
    daily_loss: float = 0.0
    current_day: int = -1
    # Track net open positions: open_buys means long slots, open_sells means short slots
    open_buys: int = 0
    open_sells: int = 0

    for trade in trades:
        # Reset daily loss on new day (using ms -> day boundary)
        trade_day = trade.timestamp_ms // 86_400_000
        if trade_day != current_day:
            current_day = trade_day
            daily_loss = 0.0

        # Parse meta for radar_score and pulse_confidence
        meta = _parse_meta(trade.meta)
        radar_score = meta.get("radar_score", 999)  # default pass
        pulse_confidence = meta.get("pulse_confidence", 100.0)  # default pass

        # Determine if this trade closes an existing position or opens a new one.
        # A buy closes a short (if open_sells > 0), otherwise opens a long.
        # A sell closes a long (if open_buys > 0), otherwise opens a short.
        is_exit_meta = meta.get("exit", False) or "close" in trade.meta.lower()
        if trade.side == "buy":
            is_closing = open_sells > 0 or is_exit_meta
        else:
            is_closing = open_buys > 0 or is_exit_meta

        # Threshold filters (only apply to new entries, not position closes)
        if not is_closing:
            if radar_score < config.radar_score_threshold:
                continue
            if pulse_confidence < config.pulse_confidence_threshold:
                continue

        # Daily loss limit gate (only block new entries)
        if daily_loss >= config.daily_loss_limit and not is_closing:
            continue

        # Max same direction filter (only block new entries)
        if not is_closing:
            if trade.side == "buy" and open_buys >= config.max_same_direction:
                continue
            if trade.side == "sell" and open_sells >= config.max_same_direction:
                continue

        # Update open position tracking
        if is_closing:
            if trade.side == "buy" and open_sells > 0:
                open_sells -= 1
            elif trade.side == "sell" and open_buys > 0:
                open_buys -= 1
        else:
            if trade.side == "buy":
                open_buys += 1
            else:
                open_sells += 1

        filtered.append(trade)

        # Track daily loss (approximate from fee + negative pnl signal)
        if trade.fee > 0:
            daily_loss += trade.fee

    return filtered


def _parse_meta(meta_str: str) -> dict:
    """Try to parse meta as JSON; return empty dict on failure."""
    if not meta_str:
        return {}
    try:
        result = json.loads(meta_str)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="APEX backtest harness")
    parser.add_argument("--config", required=True, help="Path to apex_config.json")
    parser.add_argument("--trades", default="data/cli/trades.jsonl",
                        help="Path to trades JSONL file")
    args = parser.parse_args(argv)

    config = ApexConfig.from_json(args.config)
    trades = load_trades(args.trades)
    filtered = replay_with_config(trades, config)

    # Quality gate
    engine = ReflectEngine()
    metrics = engine.compute(filtered)

    if metrics.total_round_trips < 5:
        print(f"REJECT: too few trades ({metrics.total_round_trips} round trips from {len(filtered)} filtered trades)")
        return 1

    # Output in autoresearch-parseable format
    print(f"net_pnl: {metrics.net_pnl:.2f}")
    print(f"win_rate: {metrics.win_rate:.1f}")
    print(f"fdr: {metrics.fdr:.1f}")
    print(f"trades: {metrics.total_round_trips}")
    print(f"profit_factor: {metrics.net_profit_factor:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
