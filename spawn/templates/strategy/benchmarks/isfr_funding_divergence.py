"""ISFR + funding divergence strategy.

Uses ISFR as a macro stress feature and Hyperliquid funding as a local perp
microstructure feature. The intent is to give AutoResearch a concrete template
for searching ISFR-conditioned strategies without making ISFR a tradeable asset.
"""

import numpy as np
from prepare import Signal, PortfolioState, BarData

ACTIVE_SYMBOLS = ["BTC", "ETH", "SOL"]
LOOKBACK = 72
POSITION_SIZE_PCT = 0.08
ISFR_Z_ENTRY = 1.0
FUNDING_Z_ENTRY = 0.8
MAX_EXPOSURE_PCT = 0.30


class Strategy:
    def on_bar(self, bar_data: dict, portfolio: PortfolioState) -> list:
        signals = []
        equity = portfolio.equity if portfolio.equity > 0 else portfolio.cash
        total_exposure = sum(abs(v) for v in portfolio.positions.values())

        for symbol in ACTIVE_SYMBOLS:
            if symbol not in bar_data:
                continue
            bd: BarData = bar_data[symbol]
            if len(bd.history) < LOOKBACK or "isfr_rate" not in bd.history:
                continue

            funding = bd.history["funding_rate"].values[-LOOKBACK:]
            isfr = bd.history["isfr_rate"].values[-LOOKBACK:]
            if np.std(funding) == 0 or np.std(isfr) == 0:
                continue

            funding_z = (funding[-1] - np.mean(funding)) / np.std(funding)
            isfr_z = (isfr[-1] - np.mean(isfr)) / np.std(isfr)
            current_pos = portfolio.positions.get(symbol, 0.0)
            target_notional = current_pos

            if total_exposure < equity * MAX_EXPOSURE_PCT:
                if isfr_z < -ISFR_Z_ENTRY and funding_z < FUNDING_Z_ENTRY:
                    target_notional = equity * POSITION_SIZE_PCT
                elif isfr_z > ISFR_Z_ENTRY or funding_z > FUNDING_Z_ENTRY:
                    target_notional = -equity * POSITION_SIZE_PCT

            if abs(target_notional - current_pos) > 1.0:
                signals.append(Signal(symbol=symbol, target_position=target_notional))

        return signals
