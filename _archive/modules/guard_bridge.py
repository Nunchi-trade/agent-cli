"""Guard Bridge — bridges the pure Guard engine with I/O (persistence, logging).

Can be used:
  1. Standalone (with StandaloneGuardRunner providing the tick loop)
  2. Composed into TradingEngine (called after each engine tick)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from modules.guard_config import GuardConfig
from modules.guard_state import GuardState, GuardStateStore
from modules.trailing_stop import GuardResult, TrailingStopEngine

log = logging.getLogger("guard_bridge")


class GuardBridge:
    """Manages one Guard for one position.

    Owns: Guard engine instance, state, persistence.
    Does NOT own: price fetching or order placement (injected by caller).
    """

    def __init__(
        self,
        config: GuardConfig,
        state: GuardState,
        store: Optional[GuardStateStore] = None,
    ):
        self.engine = TrailingStopEngine(config)
        self.config = config
        self.state = state
        self.store = store or GuardStateStore()

    def check(self, price: float) -> GuardResult:
        """Run one Guard evaluation cycle. Persists state automatically."""
        result = self.engine.evaluate(price, self.state)
        self.state = result.state
        self.state.last_check_ts = int(time.time() * 1000)

        self.store.save(self.state, self.config.to_dict())

        log.info(
            "GUARD [%s] price=%.4f ROE=%.1f%% tier=%d floor=%.4f -> %s: %s",
            self.state.position_id,
            price,
            result.roe_pct,
            self.state.current_tier_index,
            result.effective_floor,
            result.action.value,
            result.reason,
        )

        return result

    def mark_closed(self, price: float, reason: str) -> None:
        """Mark the position as closed in state and persist."""
        self.state.closed = True
        self.state.close_reason = reason
        self.state.close_price = price
        self.state.close_ts = int(time.time() * 1000)
        self.store.save(self.state, self.config.to_dict())
        log.info("GUARD [%s] marked closed: %s", self.state.position_id, reason)

    @property
    def is_active(self) -> bool:
        return not self.state.closed

    def sync_exchange_sl(self, hl, instrument: str, max_retries: int = 3) -> None:
        """Place or update exchange-level SL to match current GUARD floor.

        Retries with exponential backoff if trigger order placement fails.
        """
        if not self.is_active:
            return

        floor_price = self._compute_current_floor()
        if floor_price <= 0:
            return

        close_side = "sell" if self.state.direction == "long" else "buy"

        # Cancel old trigger if exists
        old_oid = self.state.exchange_sl_oid
        if old_oid:
            hl.cancel_trigger_order(instrument, old_oid)

        # Place new trigger order with retry
        new_oid = None
        for attempt in range(max_retries):
            new_oid = hl.place_trigger_order(
                instrument=instrument, side=close_side,
                size=self.state.position_size, trigger_price=floor_price,
            )
            if new_oid:
                break
            delay = 2 ** attempt  # 1s, 2s, 4s
            log.warning(
                "Exchange SL placement failed for [%s] (attempt %d/%d), retrying in %ds",
                self.state.position_id, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)

        if not new_oid:
            log.error(
                "Exchange SL placement FAILED after %d retries for [%s] — "
                "position has no exchange-side stop loss! Local Guard still active.",
                max_retries, self.state.position_id,
            )

        self.state.exchange_sl_oid = new_oid or ""
        self._persist()

    def cancel_exchange_sl(self, hl, instrument: str) -> None:
        """Cancel exchange-level SL (on position close)."""
        if self.state.exchange_sl_oid:
            hl.cancel_trigger_order(instrument, self.state.exchange_sl_oid)
            self.state.exchange_sl_oid = ""

    def _compute_current_floor(self) -> float:
        """Compute the current effective floor price for exchange SL."""
        cfg = self.config
        s = self.state

        if s.current_tier_index < 0:
            # Phase 1 — use absolute floor if set, else compute from retrace
            if cfg.phase1_absolute_floor > 0:
                return cfg.phase1_absolute_floor
            # Fallback: trailing floor from high water
            retrace = cfg.phase1_retrace
            if s.direction == "long":
                return s.high_water * (1 - retrace)
            else:
                return s.high_water * (1 + retrace)
        else:
            # Phase 2 — use tier floor
            return self.engine._tier_floor_price(s.current_tier_index, s)

    def _persist(self) -> None:
        """Persist current state to store."""
        self.store.save(self.state, self.config.to_dict())

    @classmethod
    def from_store(
        cls,
        position_id: str,
        store: Optional[GuardStateStore] = None,
    ) -> Optional[GuardBridge]:
        """Restore a guard from persisted state file."""
        store = store or GuardStateStore()
        data = store.load(position_id)
        if data is None:
            return None
        state = GuardState.from_dict(data["state"])
        config = GuardConfig.from_dict(data.get("config", {}))
        return cls(config=config, state=state, store=store)
