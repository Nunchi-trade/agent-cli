"""
Autotrader strategy file. This is the ONLY file the agent modifies.

Start simple. Beat the existing Nunchi production strategies.
The agent should discover novel strategies, not just tune parameters.

Usage: imported by backtest.py — do not run directly.
"""

import numpy as np
import pandas as pd
from prepare import Signal, PortfolioState, BarData

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

LOOKBACK = 24
POSITION_SIZE_PCT = 0.10
STOP_LOSS_PCT = 0.03
TAKE_PROFIT_PCT = 0.06
MOMENTUM_THRESHOLD = 0.02
ISFR_DROP_THRESHOLD = 0.0005
ISFR_SPIKE_THRESHOLD = 0.0005
ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]

# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class Strategy:
    def __init__(self):
        self.entry_prices = {}

    def on_bar(self, bar_data: dict, portfolio: PortfolioState) -> list:
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd = bar_data[symbol]
            if len(bd.history) < LOOKBACK:
                continue

            closes = bd.history["close"].values[-LOOKBACK:]
            isfr_series = bd.history.get("isfr_rate", pd.Series([0.0])).values[-LOOKBACK:]
            returns = (closes[-1] - closes[0]) / closes[0]
            isfr_delta = 0.0
            if len(isfr_series) >= LOOKBACK:
                isfr_delta = float(isfr_series[-1] - np.mean(isfr_series))

            current_pos = portfolio.positions.get(symbol, 0.0)
            target_notional = current_pos

            if returns > MOMENTUM_THRESHOLD or isfr_delta < -ISFR_DROP_THRESHOLD:
                target_notional = equity * POSITION_SIZE_PCT
            elif returns < -MOMENTUM_THRESHOLD or isfr_delta > ISFR_SPIKE_THRESHOLD:
                target_notional = -equity * POSITION_SIZE_PCT

            if current_pos != 0 and symbol in self.entry_prices:
                entry = self.entry_prices[symbol]
                if entry > 0:
                    pnl_pct = (bd.close - entry) / entry
                    if current_pos < 0:
                        pnl_pct = -pnl_pct
                    if pnl_pct < -STOP_LOSS_PCT or pnl_pct > TAKE_PROFIT_PCT:
                        target_notional = 0.0

            if abs(target_notional - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target_notional))
                if target_notional != 0 and current_pos == 0:
                    self.entry_prices[symbol] = bd.close
                elif target_notional == 0:
                    self.entry_prices.pop(symbol, None)

        return signals
