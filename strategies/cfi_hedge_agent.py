"""CFI v2 funding-cost hedge agent.

Bridges the pure CFI v2 hedge math (`strategies/cfi_hedge.py`) and the
deterministic auto-open decision (`modules/hedge_auto.py`) into the
`BaseStrategy.on_tick` surface so the hedge can run inside the standard
strategy loop alongside the inventory-control `HedgeAgent`.

This is the canonical "hedge" strategy: it neutralises funding-rate cost
on an existing perp position by opening a 1/L CFI v2 (yex:{COIN}SWP) leg.
Contrast with `strategies/hedge_agent.py`, which is a delta/inventory
reducer.

`on_tick` is pure given its inputs — no network, no signing. It reads the
position notional from `StrategyContext.position_notional` (falling back to
`position_qty × mid_price`), uses the live snapshot's funding rate as the
current floating rate, and seeds K2 from the deployed profile. The same
safety caps that gate `hl hedge auto` (per-action ceiling, daily cap,
min-interval) are applied via `compute_hedge_open_action`, so the strategy
and the CLI verb agree on when a hedge fires.
"""
from __future__ import annotations

import time
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision, instrument_to_coin
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext

from strategies.cfi_hedge import (
    build_cfi_hedge_proposal,
    get_cfi_profile,
)
from modules.hedge_auto import (
    DailyHedgeState,
    HedgeOpenAction,
    HedgePolicy,
    compute_hedge_open_action,
    today_utc_iso,
)


class CfiHedgeAgent(BaseStrategy):
    """Open a 1/L CFI v2 hedge against an existing perp position.

    Per-tick flow:
      1. Resolve the snapshot's coin to a deployed CFI v2 profile (BTC → BTCSWP).
      2. Read the current perp notional from the context.
      3. Run the pure `compute_hedge_open_action` gate (trigger + caps + interval).
      4. If it fires, size the CFI v2 leg via `build_cfi_hedge_proposal`
         (1/L ratio) and emit a `place_order` StrategyDecision on the
         yex:{COIN}SWP instrument.

    State (daily action counter / last-action timestamp) is held in-memory
    for the life of the strategy instance — the standalone `hl hedge auto`
    verb is the durable, disk-backed path.
    """

    def __init__(
        self,
        strategy_id: str = "cfi_hedge",
        notional_trigger: float = 100_000.0,
        max_hedge_notional: float = 50_000.0,
        max_per_day: int = 5,
        min_interval_seconds: int = 300,
    ):
        super().__init__(strategy_id=strategy_id)
        self.notional_trigger = notional_trigger
        self.max_hedge_notional = max_hedge_notional
        self.max_per_day = max_per_day
        self.min_interval_seconds = min_interval_seconds
        # In-memory daily counters (CLI verb owns the disk-backed copy).
        self._daily = DailyHedgeState.fresh(today_utc_iso())
        # Coins already hedged in this strategy session.
        self._active_coins: set[str] = set()

    def on_tick(
        self,
        snapshot: MarketSnapshot,
        context: Optional[StrategyContext] = None,
    ) -> List[StrategyDecision]:
        if snapshot.mid_price <= 0:
            return []

        # Normalise the snapshot instrument to a bare ticker (BTC-PERP → BTC,
        # yex:BTCSWP → BTCSWP) so it keys into the deployed CFI v2 profiles.
        coin = instrument_to_coin(snapshot.instrument)
        if ":" in coin:
            coin = coin.split(":", 1)[1]
        coin = coin.upper()
        profile = get_cfi_profile(coin)
        if profile is None:
            return []

        # Perp notional: prefer the context's notional, else derive from qty.
        if context is not None and context.position_notional:
            perp_notional = abs(context.position_notional)
        elif context is not None:
            perp_notional = abs(context.position_qty) * snapshot.mid_price
        else:
            perp_notional = 0.0

        now_ms = int(time.time() * 1000)
        self._daily = self._daily.reset_if_new_day(today_utc_iso())

        policy = HedgePolicy(
            notional_trigger_usd=self.notional_trigger,
            coins=(coin,),
            max_hedge_notional_usd=self.max_hedge_notional,
            max_per_day_actions=self.max_per_day,
            min_interval_seconds=self.min_interval_seconds,
        )

        action, _reason = compute_hedge_open_action(
            coin=coin,
            perp_notional_usd=perp_notional,
            active_hedge_coins=self._active_coins,
            profile_vol_mult_l=profile.vol_mult_l,
            policy=policy,
            daily=self._daily,
            now_ms=now_ms,
        )
        if action is None:
            return []

        decision = self._action_to_decision(action, snapshot, profile, context)
        if decision is None:
            return []

        # Record so caps/interval/dedupe hold across subsequent ticks.
        self._daily = self._daily.record(action, now_ms)
        self._active_coins.add(coin)
        return [decision]

    def _action_to_decision(
        self,
        action: HedgeOpenAction,
        snapshot: MarketSnapshot,
        profile,
        context: Optional[StrategyContext],
    ) -> Optional[StrategyDecision]:
        """Map a HedgeOpenAction → a CFI v2 leg StrategyDecision.

        Sizing follows `build_cfi_hedge_proposal`: the hedge leg side matches
        the existing perp side, the notional is `action.hedge_notional_usd`
        (already 1/L and capped), and the order is priced off the profile
        baseline with IOC slippage. No network — funding comes from the
        snapshot, K2 is seeded from the deployed profile.
        """
        # Existing perp side. Default long when context doesn't say otherwise.
        side_is_long = True
        if context is not None and context.position_qty < 0:
            side_is_long = False
        perp_side = "long" if side_is_long else "short"

        from strategies.cfi_hedge import HLPositionSummary

        position = HLPositionSummary(
            coin=action.coin,
            side=perp_side,
            size_coin=abs(context.position_qty) if context else 0.0,
            entry_px=snapshot.mid_price,
            mark_px=snapshot.mid_price,
            notional_usd=action.perp_notional_usd,
            leverage=profile.vol_mult_l,
            unrealized_pnl_usd=context.unrealized_pnl if context else 0.0,
            cum_funding_usd=0.0,
        )
        proposal = build_cfi_hedge_proposal(
            user_address="strategy",
            position=position,
            current_funding_hr=snapshot.funding_rate,
            k_fixed_hr=profile.fixed_leg_initial,
        )
        if proposal is None:
            return None

        # CFI v2 leg side matches the existing perp side (identity derivation).
        hedge_leg = proposal.legs[1]
        is_buy = hedge_leg.side == "long"

        # Price off the baseline wire price (offline-safe; SDK rounds to tick).
        wire_px = proposal.profile.baseline_b0
        if wire_px <= 0:
            return None
        size = action.hedge_notional_usd / wire_px
        slippage = 1.002 if is_buy else 0.998
        price = wire_px * slippage

        return StrategyDecision(
            action="place_order",
            instrument=proposal.profile.cfi_instrument,
            side="buy" if is_buy else "sell",
            size=round(size, 6),
            limit_price=round(price, 4),
            order_type="Ioc",
            meta={
                "signal": "cfi_hedge_open",
                "coin": action.coin,
                "perp_notional_usd": round(action.perp_notional_usd, 2),
                "hedge_notional_usd": round(action.hedge_notional_usd, 2),
                "hedge_ratio": proposal.hedge_ratio,
                "locked_k2_apy": proposal.k_fixed_apy,
                "current_funding_apy": proposal.current_funding_apy,
                "reason": action.reason,
            },
        )
