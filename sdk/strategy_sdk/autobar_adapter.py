"""AutoBarStrategy — bridge an autoresearch (hourly `on_bar`) strategy into the
tick-level `on_tick` engine.

An autoresearch project (see `hl strategy new` / `hl autoresearch run`) exposes a
``strategy.py`` with::

    def on_bar(bar_data: dict[str, BarData], portfolio: PortfolioState) -> list[Signal]

where each ``Signal.target_position`` is a *signed USD notional* target for a
symbol (``+`` long, ``-`` short), and ``BarData`` carries a rolling history
DataFrame. That contract is hourly; agent-cli's TradingEngine is tick-level
(``BaseStrategy.on_tick`` every ``tick_interval`` seconds).

This adapter is the faithful Python port of ACC's
``server/src/strategy-deployer.ts`` translation layer:

  * Maintain a rolling deque of ~360 ten-second ticks (= 1 hour) per instrument.
  * Detect an hourly bar boundary (wall-clock hour change, with a tick-count
    fallback so it also fires under mock/replay where the clock barely moves).
  * On a boundary, fold the ticks into one OHLCV close, append to the hourly
    history, build ``BarData`` + ``PortfolioState`` and call the external
    ``on_bar`` exactly once. Hold the resulting target between bars (no
    re-evaluation mid-bar).
  * Translate each ``Signal.target_position`` (signed USD) into a delta vs the
    engine's live ``context.position_notional`` and emit an IOC
    ``StrategyDecision`` for the residual.

It deliberately does NOT modify the external strategy — it only adapts the call
shape — so the SAME ``strategy.py`` the autoresearch loop backtested runs live.
"""
from __future__ import annotations

import importlib.util
import logging
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from common.models import (
    MarketSnapshot,
    StrategyDecision,
    instrument_to_asset,
)
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

log = logging.getLogger("autobar")

# 10s ticks * 360 = 1 hour. Mirrors strategy-deployer.ts (deque maxlen 360).
TICKS_PER_HOUR = 360
# Hourly history depth handed to on_bar via BarData.history. The autotrader
# scaffold uses LOOKBACK_BARS=500; match it so live history shape == backtest.
DEFAULT_HISTORY_BARS = 500
MS_PER_HOUR = 3_600_000


def _load_external_strategy(strategy_py: Path):
    """Import an external strategy.py and return an instantiated `Strategy()`.

    The file lives outside the package tree (``~/.nunchi/strategies/<name>/``),
    so we load it by path. Its directory is prepended to ``sys.path`` first so
    its own ``from prepare import Signal, BarData, PortfolioState`` resolves
    against the sibling ``prepare.py`` in the scaffold.
    """
    strategy_py = strategy_py.expanduser().resolve()
    if not strategy_py.exists():
        raise FileNotFoundError(f"strategy file not found: {strategy_py}")

    strat_dir = str(strategy_py.parent)
    if strat_dir not in sys.path:
        sys.path.insert(0, strat_dir)

    # Unique module name so two strategies don't collide in sys.modules.
    mod_name = f"_autobar_ext_{strategy_py.parent.name}_{strategy_py.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, strategy_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build import spec for {strategy_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "Strategy"):
        raise AttributeError(
            f"{strategy_py} does not define a `Strategy` class "
            "(autoresearch scaffold contract)"
        )
    return module


class _TickBar:
    """Accumulates 10s ticks into a single OHLCV hourly bar."""

    __slots__ = ("open", "high", "low", "close", "volume", "funding_rate", "n")

    def __init__(self, price: float, funding_rate: float = 0.0) -> None:
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = 0.0
        self.funding_rate = funding_rate
        self.n = 1

    def update(self, price: float, funding_rate: float, volume_delta: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.funding_rate = funding_rate
        self.volume += max(0.0, volume_delta)
        self.n += 1


class AutoBarStrategy(BaseStrategy):
    """Adapts an external hourly ``on_bar`` strategy to ``on_tick``.

    Args:
        strategy_id: engine strategy id (also the project name).
        strategy_path: path to the external ``strategy.py`` exposing ``Strategy``
            with ``on_bar(bar_data, portfolio) -> list[Signal]``. Defaults to
            ``~/.nunchi/strategies/<strategy_id>/strategy.py``.
        ticks_per_hour: ticks that fold into one bar AND the tick-count fallback
            boundary. Lower it for fast tests/replay (e.g. 10).
        history_bars: hourly bars kept in ``BarData.history``.
        initial_capital: equity floor used to seed ``PortfolioState`` before the
            engine reports real account value.
        backtest_score: the strategy's kept backtest score, used as the drift
            baseline (read from results.tsv by the runner). ``None`` disables
            score-vs-live drift comparison.
        drift_window: number of completed bars of live returns to keep for the
            rolling live-Sharpe drift estimate.
        retrain_callback: optional ``callable(reason: str, stats: dict)`` invoked
            once when drift crosses the threshold (retrain-trigger hook).
    """

    def __init__(
        self,
        strategy_id: str = "autobar",
        strategy_path: Optional[str] = None,
        ticks_per_hour: int = TICKS_PER_HOUR,
        history_bars: int = DEFAULT_HISTORY_BARS,
        initial_capital: float = 100_000.0,
        backtest_score: Optional[float] = None,
        drift_window: int = 168,
        retrain_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        **_ignored: Any,
    ) -> None:
        super().__init__(strategy_id=strategy_id)

        if strategy_path is None:
            strategy_path = str(
                Path.home() / ".nunchi" / "strategies" / strategy_id / "strategy.py"
            )
        self.strategy_path = Path(strategy_path)

        module = _load_external_strategy(self.strategy_path)
        self._ext_module = module
        self._ext = module.Strategy()
        # The scaffold's dataclasses live in the sibling prepare.py; pull them in
        # by way of the strategy module's own imports so we build the exact types
        # on_bar expects.
        self._Signal = getattr(module, "Signal", None) or self._import_from_prepare("Signal")
        self._BarData = getattr(module, "BarData", None) or self._import_from_prepare("BarData")
        self._PortfolioState = (
            getattr(module, "PortfolioState", None)
            or self._import_from_prepare("PortfolioState")
        )

        self.ticks_per_hour = max(1, int(ticks_per_hour))
        self.history_bars = max(1, int(history_bars))
        self.initial_capital = float(initial_capital)

        # Rolling tick buffer + hourly history, per instrument (multi-symbol safe).
        self._cur_bar: Dict[str, _TickBar] = {}
        self._ticks_in_bar: Dict[str, int] = {}
        self._hourly: Dict[str, deque] = {}
        self._last_hour_idx: Dict[str, int] = {}
        # Last computed target USD notional per symbol — held between bars.
        self._target_usd: Dict[str, float] = {}
        # avg entry price per symbol, for PortfolioState.entry_prices.
        self._entry_px: Dict[str, float] = {}

        # ── Guardrails ──
        self.backtest_score = backtest_score
        self.drift_window = max(8, int(drift_window))
        self._live_returns: deque = deque(maxlen=self.drift_window)
        self._last_bar_equity: Optional[float] = None
        self.retrain_callback = retrain_callback
        self._retrain_fired = False
        self.drift_state: Dict[str, Any] = {
            "live_sharpe": None,
            "live_return_pct": None,
            "backtest_score": backtest_score,
            "diverged": False,
            "bars_observed": 0,
        }

    # ------------------------------------------------------------------ helpers
    def _import_from_prepare(self, name: str):
        prep = self.strategy_path.parent / "prepare.py"
        if not prep.exists():
            raise ImportError(
                f"{name} not found in strategy module and no prepare.py at {prep}"
            )
        spec = importlib.util.spec_from_file_location(
            f"_autobar_prep_{self.strategy_path.parent.name}", prep
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import prepare.py at {prep}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        obj = getattr(mod, name, None)
        if obj is None:
            raise ImportError(f"{name} not defined in {prep}")
        return obj

    def _hour_index(self, snapshot: MarketSnapshot) -> int:
        """Wall-clock hour bucket. 0 if no timestamp (replay/mock fallback)."""
        if snapshot.timestamp_ms > 0:
            return snapshot.timestamp_ms // MS_PER_HOUR
        return 0

    def _history_df(self, instrument: str):
        """Build the BarData.history DataFrame from completed hourly bars.

        Includes an ``isfr_rate`` column (0.0 live) so scaffolds that read
        ``history["isfr_rate"]`` don't KeyError — matching prepare.py's schema.
        """
        import pandas as pd

        bars = list(self._hourly.get(instrument, ()))
        if not bars:
            return pd.DataFrame(
                columns=[
                    "timestamp", "open", "high", "low",
                    "close", "volume", "funding_rate", "isfr_rate",
                ]
            )
        return pd.DataFrame(bars)

    # -------------------------------------------------------------------- bridge
    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        if snapshot.mid_price <= 0:
            return []

        instrument = snapshot.instrument
        symbol = instrument_to_asset(instrument)

        # 1) Fold this tick into the in-progress bar.
        cur = self._cur_bar.get(instrument)
        if cur is None:
            self._cur_bar[instrument] = _TickBar(snapshot.mid_price, snapshot.funding_rate)
            self._ticks_in_bar[instrument] = 1
        else:
            cur.update(snapshot.mid_price, snapshot.funding_rate, snapshot.volume_24h * 0.0)
            self._ticks_in_bar[instrument] = self._ticks_in_bar.get(instrument, 0) + 1

        # 2) Decide if this tick closes a bar. Two triggers (either fires):
        #    a) wall-clock hour rolled over (real-time deployment), or
        #    b) we've accumulated `ticks_per_hour` ticks (replay/mock/backfill).
        hour_idx = self._hour_index(snapshot)
        prev_hour = self._last_hour_idx.get(instrument)
        wallclock_boundary = (
            prev_hour is not None and hour_idx > prev_hour and snapshot.timestamp_ms > 0
        )
        self._last_hour_idx[instrument] = hour_idx
        count_boundary = self._ticks_in_bar.get(instrument, 0) >= self.ticks_per_hour

        if not (wallclock_boundary or count_boundary):
            # Mid-bar: hold the last target. Re-issue the residual only if the
            # engine reports we've drifted off target (e.g. partial fill).
            return self._residual_decisions(snapshot, context)

        # 3) Bar boundary — finalize the bar and run on_bar exactly once.
        closed = self._cur_bar.pop(instrument)
        self._ticks_in_bar[instrument] = 0
        if closed is None:
            return []

        bar_row = {
            "timestamp": (hour_idx * MS_PER_HOUR) if snapshot.timestamp_ms > 0
            else snapshot.timestamp_ms,
            "open": closed.open,
            "high": closed.high,
            "low": closed.low,
            "close": closed.close,
            "volume": closed.volume,
            "funding_rate": closed.funding_rate,
            "isfr_rate": 0.0,
        }
        hq = self._hourly.setdefault(instrument, deque(maxlen=self.history_bars))
        hq.append(bar_row)

        # Start the next in-progress bar seeded with the current tick.
        self._cur_bar[instrument] = _TickBar(snapshot.mid_price, snapshot.funding_rate)
        self._ticks_in_bar[instrument] = 1

        decisions = self._run_on_bar(symbol, instrument, snapshot, context)

        # 4) Guardrail: update drift estimate on each completed bar.
        self._update_drift(context)

        return decisions

    def _run_on_bar(
        self,
        symbol: str,
        instrument: str,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext],
    ) -> List[StrategyDecision]:
        """Build BarData+PortfolioState, call external on_bar, translate signals."""
        history_df = self._history_df(instrument)

        last = history_df.iloc[-1]
        bar = self._BarData(
            symbol=symbol,
            timestamp=int(last["timestamp"]),
            open=float(last["open"]),
            high=float(last["high"]),
            low=float(last["low"]),
            close=float(last["close"]),
            volume=float(last["volume"]),
            funding_rate=float(last["funding_rate"]),
            history=history_df,
        )

        # PortfolioState from live engine context. positions are signed USD
        # notional keyed by bare symbol — matching the backtest engine.
        pos_notional = float(context.position_notional) if context else self._target_usd.get(symbol, 0.0)
        equity = self.initial_capital
        if context is not None:
            # account value isn't on context; approximate equity with capital +
            # realized + unrealized so position-sizing-by-equity strategies work.
            equity = self.initial_capital + float(context.realized_pnl) + float(context.unrealized_pnl)
        positions = {symbol: pos_notional} if abs(pos_notional) > 0.0 else {}
        entry_prices = dict(self._entry_px)
        if abs(pos_notional) > 0.0 and symbol not in entry_prices:
            entry_prices[symbol] = snapshot.mid_price

        portfolio = self._PortfolioState(
            cash=max(0.0, equity - sum(abs(v) for v in positions.values())),
            positions=positions,
            entry_prices=entry_prices,
            equity=equity,
            timestamp=bar.timestamp,
        )

        try:
            signals = self._ext.on_bar({symbol: bar}, portfolio) or []
        except Exception as e:  # never let a strategy bug kill the tick loop
            log.error("on_bar raised for %s: %s", self.strategy_id, e, exc_info=True)
            return []

        decisions: List[StrategyDecision] = []
        for sig in signals:
            sig_symbol = getattr(sig, "symbol", symbol)
            if sig_symbol != symbol:
                # Single-instrument engine run; ignore cross-symbol signals here.
                # (Multi-symbol live trading runs one engine per instrument.)
                log.debug("ignoring signal for %s (engine bound to %s)", sig_symbol, symbol)
                continue
            target_usd = float(getattr(sig, "target_position", 0.0))
            self._target_usd[symbol] = target_usd
            if target_usd == 0.0:
                self._entry_px.pop(symbol, None)
            else:
                self._entry_px[symbol] = snapshot.mid_price
            decisions.extend(self._target_to_decision(target_usd, snapshot, context))

        return decisions

    def _target_to_decision(
        self,
        target_usd: float,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext],
    ) -> List[StrategyDecision]:
        """Translate a signed-USD target into an IOC order for the residual.

        delta_usd = target_usd - current_position_notional
        size_base = |delta_usd| / mid_price
        side      = buy if delta_usd > 0 else sell

        Mirrors strategy-deployer.ts: USD target -> delta vs current -> order.
        """
        current_usd = float(context.position_notional) if context else 0.0

        # Guardrail: reduce_only / safe_mode — never *increase* exposure.
        if context is not None and (context.reduce_only or context.safe_mode):
            if abs(target_usd) >= abs(current_usd):
                if context.safe_mode:
                    log.warning("safe_mode: suppressing new/added risk (target=%.2f cur=%.2f)",
                                target_usd, current_usd)
                return []
            # Allow a reducing order toward the smaller target only.

        delta_usd = target_usd - current_usd
        if abs(delta_usd) < 1.0:  # < $1 change — skip (matches backtest engine)
            return []

        mid = snapshot.mid_price
        if mid <= 0:
            return []
        size_base = abs(delta_usd) / mid
        if size_base <= 0:
            return []

        side = "buy" if delta_usd > 0 else "sell"
        return [
            StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side=side,
                size=size_base,
                limit_price=mid,
                order_type="Ioc",
                meta={
                    "source": "autobar",
                    "target_usd": target_usd,
                    "delta_usd": delta_usd,
                },
            )
        ]

    def _residual_decisions(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext],
    ) -> List[StrategyDecision]:
        """Between bars: re-assert the held target if live position drifted.

        on_bar is NOT re-run; we only top up / trim toward the last target if a
        partial fill (or external move) left a residual > $1. This keeps the held
        target without re-evaluating signals mid-bar.
        """
        symbol = instrument_to_asset(snapshot.instrument)
        if symbol not in self._target_usd:
            return []
        return self._target_to_decision(self._target_usd[symbol], snapshot, context)

    # --------------------------------------------------------------- guardrails
    def _update_drift(self, context: Optional[StrategyContext]) -> None:
        """Track live realized return / Sharpe per bar and compare to the
        strategy's backtest score. Warn (and fire the retrain hook once) on
        divergence.

        Divergence rule (intentionally simple): once we have a full window of
        live bars, flag if the annualized live Sharpe is negative while the kept
        backtest score was positive, OR if live Sharpe falls below half the
        backtest score. Both are coarse but actionable "the live regime no
        longer matches what we optimized" signals.
        """
        if context is None:
            return
        equity = self.initial_capital + float(context.realized_pnl) + float(context.unrealized_pnl)
        if self._last_bar_equity is not None and self._last_bar_equity > 0:
            self._live_returns.append((equity - self._last_bar_equity) / self._last_bar_equity)
        self._last_bar_equity = equity
        self.drift_state["bars_observed"] += 1

        n = len(self._live_returns)
        if n < self.drift_window:
            return

        import numpy as np

        r = np.array(self._live_returns, dtype=float)
        live_ret_pct = float((np.prod(1.0 + r) - 1.0) * 100.0)
        std = r.std()
        live_sharpe = float((r.mean() / std) * math.sqrt(8760)) if std > 0 else 0.0
        self.drift_state["live_sharpe"] = round(live_sharpe, 4)
        self.drift_state["live_return_pct"] = round(live_ret_pct, 4)

        if self.backtest_score is None:
            return

        diverged = False
        reason = ""
        if self.backtest_score > 0 and live_sharpe < 0:
            diverged = True
            reason = (
                f"live Sharpe {live_sharpe:.2f} negative vs positive backtest "
                f"score {self.backtest_score:.2f}"
            )
        elif live_sharpe < 0.5 * self.backtest_score:
            diverged = True
            reason = (
                f"live Sharpe {live_sharpe:.2f} < 50% of backtest score "
                f"{self.backtest_score:.2f}"
            )

        self.drift_state["diverged"] = diverged
        if diverged:
            log.warning("DRIFT [%s]: %s (live_ret=%.2f%% over %d bars)",
                        self.strategy_id, reason, live_ret_pct, n)
            if self.retrain_callback is not None and not self._retrain_fired:
                self._retrain_fired = True
                try:
                    self.retrain_callback(reason, dict(self.drift_state))
                except Exception as e:
                    log.error("retrain_callback raised: %s", e)
