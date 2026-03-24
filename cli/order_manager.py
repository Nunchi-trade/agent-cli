"""Order lifecycle management — place, track, cancel."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

from common.models import MarketSnapshot, StrategyDecision
from execution.parent_order import ParentOrder
from execution.routing import OrderRouter
from execution.twap import TWAPExecutor, ChildSlice
from parent.hl_proxy import HLFill

if TYPE_CHECKING:
    from cli.hl_adapter import DirectHLProxy, DirectMockProxy
    from common.venue_adapter import VenueAdapter

log = logging.getLogger("order_manager")


class OrderManager:
    """Manages order lifecycle each tick: cancel stale -> place new -> collect fills.

    Respects each StrategyDecision's order_type field:
    - "Gtc" (default): rests on book as passive limit order (MM strategies)
    - "Ioc": crosses spread for immediate fill (directional strategies)
    - "Alo": maker-only, rejects if it would cross

    Each tick cancels stale orders before placing fresh quotes.
    Supports TWAP execution for large orders via execution_algo meta field.
    """

    def __init__(
        self,
        hl,  # VenueAdapter (or DirectHLProxy | DirectMockProxy for backwards compat)
        instrument: str = "ETH-PERP",
        dry_run: bool = False,
        builder: dict = None,
        router: Optional[OrderRouter] = None,
    ):
        self.hl = hl
        self.instrument = instrument
        self.dry_run = dry_run
        self._builder = builder
        self._router = router
        self._total_placed = 0
        self._total_filled = 0
        self._twap = TWAPExecutor()

    def update(
        self,
        decisions: List[StrategyDecision],
        snapshot: MarketSnapshot,
    ) -> List[HLFill]:
        """Full tick cycle: cancel open orders -> TWAP slices -> place new -> return fills."""
        fills: List[HLFill] = []

        # 1. Cancel any lingering open orders (safety net for IOC leftovers)
        self.cancel_all()

        # 2. Process active TWAP orders
        twap_slices = self._twap.on_tick(snapshot)
        for s in twap_slices:
            fill = self._execute_child_slice(s)
            if fill is not None:
                fills.append(fill)
                self._twap.record_fill(
                    s.parent_order_id, fill.size, fill.price,
                    snapshot.timestamp_ms,
                )

        # 3. Place new orders from strategy decisions
        for d in decisions:
            if d.action != "place_order" or d.size <= 0 or d.limit_price <= 0:
                continue

            # Route to TWAP if execution_algo says so
            if d.meta.get("execution_algo") == "twap":
                parent = ParentOrder(
                    instrument=d.instrument or self.instrument,
                    side=d.side,
                    target_qty=d.size,
                    algo="twap",
                    duration_ticks=d.meta.get("twap_duration_ticks", 5),
                    urgency=d.meta.get("twap_urgency", 0.7),
                    created_at_ms=snapshot.timestamp_ms,
                )
                self._twap.submit(parent)
                log.info("TWAP submitted: %s %s %.6f over %d ticks",
                         d.side.upper(), parent.instrument,
                         parent.target_qty, parent.duration_ticks)
                self._total_placed += 1
                continue

            # Determine TIF via router (if available) or use decision's order_type
            tif = d.order_type
            if self._router is not None:
                urgency = d.meta.get("urgency", 0.5)
                tif = self._router.route(d, snapshot, urgency=urgency)

            if self.dry_run:
                log.info("[DRY RUN] %s %s %.6f @ %.4f (tif=%s)",
                         d.side.upper(), d.instrument or self.instrument,
                         d.size, d.limit_price, tif)
                self._total_placed += 1
                continue

            fill = self.hl.place_order(
                instrument=d.instrument or self.instrument,
                side=d.side,
                size=d.size,
                price=d.limit_price,
                tif=tif,
                builder=self._builder,
            )
            self._total_placed += 1

            # Record routing stats
            if self._router is not None:
                if tif == "Alo":
                    size_usd = d.size * d.limit_price
                    self._router.stats.record_alo_attempt(
                        success=fill is not None, size_usd=size_usd,
                    )
                else:
                    self._router.stats.record_order(tif)

            if fill is not None:
                fills.append(fill)
                self._total_filled += 1

        return fills

    def _execute_child_slice(self, s: ChildSlice) -> HLFill | None:
        """Execute a single TWAP child slice as an IOC order."""
        if self.dry_run:
            log.info("[DRY RUN TWAP] %s %s %.6f @ %.4f",
                     s.side.upper(), s.instrument, s.size, s.price)
            self._total_placed += 1
            return None

        fill = self.hl.place_order(
            instrument=s.instrument,
            side=s.side,
            size=s.size,
            price=s.price,
            tif="Ioc",
            builder=self._builder,
        )
        self._total_placed += 1
        if fill is not None:
            self._total_filled += 1
        return fill

    def cancel_all(self) -> int:
        """Cancel all open orders for the instrument."""
        if self.dry_run:
            return 0
        open_orders = self.hl.get_open_orders(self.instrument)
        cancelled = 0
        for order in open_orders:
            oid = order.get("oid", "")
            if oid and self.hl.cancel_order(self.instrument, oid):
                cancelled += 1
        if cancelled:
            log.info("Cancelled %d open orders", cancelled)
        return cancelled

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total_placed": self._total_placed,
            "total_filled": self._total_filled,
        }
