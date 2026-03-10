"""SmartMoneyTracker — monitors whale/smart-money addresses on Hyperliquid."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.smart_money.config import SmartMoneyConfig

log = logging.getLogger("smart_money")


@dataclass
class WalletSnapshot:
    """Snapshot of a tracked wallet's positions."""
    address: str
    positions: Dict[str, Dict[str, Any]]  # coin -> {direction, size_usd, entry_price}
    timestamp_ms: int = 0


class SmartMoneyTracker:
    """Polls HL Info API to track position changes of watched addresses.

    Stateless polling model — same pattern as radar/movers.
    """

    def __init__(self, config: SmartMoneyConfig):
        self.config = config
        self._prev_snapshots: Dict[str, WalletSnapshot] = {}  # address -> last snapshot
        self._tick_count = 0

    def scan(self, hl) -> List[Dict[str, Any]]:
        """Poll all watched addresses and detect position changes.

        Args:
            hl: DirectHLProxy or DirectMockProxy (needs _info.user_state)

        Returns:
            List of signal dicts compatible with WOLF movers_signals format:
            [{"asset": "ETH", "signal_type": "SMART_MONEY", "direction": "LONG", "confidence": 85.0,
              "source_addresses": ["0x..."], "notional_usd": 50000.0}]
        """
        self._tick_count += 1
        if self._tick_count % self.config.poll_interval_ticks != 0:
            return []

        if not self.config.watch_addresses:
            return []

        current_snapshots: Dict[str, WalletSnapshot] = {}

        # Track new positions across all wallets for convergence detection
        new_positions: Dict[str, List[Dict]] = {}  # coin -> list of {direction, size_usd, address}

        for address in self.config.watch_addresses:
            snapshot = self._poll_address(hl, address)
            if snapshot is None:
                continue

            current_snapshots[address] = snapshot
            prev = self._prev_snapshots.get(address)

            # Detect new or changed positions
            changes = self._detect_changes(prev, snapshot)
            for change in changes:
                coin = change["coin"]
                if coin not in new_positions:
                    new_positions[coin] = []
                new_positions[coin].append({
                    "direction": change["direction"],
                    "size_usd": change["size_usd"],
                    "address": address,
                    "change_type": change["type"],  # "opened", "increased", "flipped"
                })

        self._prev_snapshots = current_snapshots

        # Generate signals
        signals = []
        for coin, entries in new_positions.items():
            # Determine dominant direction
            longs = [e for e in entries if e["direction"] == "LONG"]
            shorts = [e for e in entries if e["direction"] == "SHORT"]

            dominant = longs if len(longs) >= len(shorts) else shorts
            if not dominant:
                continue

            direction = dominant[0]["direction"]
            total_notional = sum(e["size_usd"] for e in dominant)
            source_addresses = [e["address"] for e in dominant]

            # Skip if below minimum
            if total_notional < self.config.min_position_usd:
                continue

            # Compute confidence: base 60 + 10 per wallet + notional bonus
            confidence = min(60.0 + len(dominant) * 10.0 + (total_notional / 100_000) * 10, 100.0)

            # Signal type based on conviction
            if len(dominant) >= self.config.conviction_threshold:
                signal_type = "HIGH_CONVICTION"
            else:
                signal_type = "SMART_MONEY"

            signals.append({
                "asset": coin,
                "signal_type": signal_type,
                "direction": direction,
                "confidence": round(confidence, 1),
                "source_addresses": source_addresses,
                "notional_usd": round(total_notional, 2),
            })

            log.info("Smart money signal: %s %s %s ($%.0f, %d wallets, conf=%.0f)",
                     signal_type, direction, coin, total_notional, len(dominant), confidence)

        return signals

    def _poll_address(self, hl, address: str) -> Optional[WalletSnapshot]:
        """Fetch positions for a single address from HL."""
        try:
            # Use the HL Info API directly
            info = getattr(hl, '_info', None) or getattr(getattr(hl, '_hl', None), '_info', None)
            if info is None:
                return None

            state = info.user_state(address)
            positions: Dict[str, Dict[str, Any]] = {}

            for asset_pos in state.get("assetPositions", []):
                pos = asset_pos.get("position", {})
                coin = pos.get("coin", "")
                size = float(pos.get("szi", "0"))
                entry_px = float(pos.get("entryPx", "0"))

                if abs(size) > 0 and coin:
                    notional = abs(size) * entry_px
                    positions[coin] = {
                        "direction": "LONG" if size > 0 else "SHORT",
                        "size": abs(size),
                        "size_usd": notional,
                        "entry_price": entry_px,
                    }

            return WalletSnapshot(
                address=address,
                positions=positions,
                timestamp_ms=int(time.time() * 1000),
            )
        except Exception as e:
            log.warning("Failed to poll address %s: %s", address[:10], e)
            return None

    def _detect_changes(self, prev: Optional[WalletSnapshot], curr: WalletSnapshot) -> List[Dict]:
        """Detect position changes between two snapshots."""
        if prev is None:
            # First scan — treat all positions as new
            changes = []
            for coin, pos in curr.positions.items():
                if pos["size_usd"] >= self.config.min_position_usd:
                    changes.append({
                        "coin": coin,
                        "type": "opened",
                        "direction": pos["direction"],
                        "size_usd": pos["size_usd"],
                    })
            return changes

        changes = []
        for coin, pos in curr.positions.items():
            prev_pos = prev.positions.get(coin)

            if prev_pos is None:
                # New position
                if pos["size_usd"] >= self.config.min_position_usd:
                    changes.append({
                        "coin": coin, "type": "opened",
                        "direction": pos["direction"], "size_usd": pos["size_usd"],
                    })
            elif prev_pos["direction"] != pos["direction"]:
                # Flipped
                if pos["size_usd"] >= self.config.min_position_usd:
                    changes.append({
                        "coin": coin, "type": "flipped",
                        "direction": pos["direction"], "size_usd": pos["size_usd"],
                    })
            elif pos["size_usd"] > prev_pos["size_usd"] * 1.2:
                # Increased by >20%
                if pos["size_usd"] >= self.config.min_position_usd:
                    changes.append({
                        "coin": coin, "type": "increased",
                        "direction": pos["direction"], "size_usd": pos["size_usd"],
                    })

        return changes
