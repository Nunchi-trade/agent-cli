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
