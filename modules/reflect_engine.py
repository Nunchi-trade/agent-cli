"""REFLECT engine — pure computation, zero I/O.

Computes trading performance metrics from raw trade records.
FIFO round-trip pairing, holding-period buckets, direction analysis,
fee drag, monster-trade dependency, and recommendation generation.

100% original implementation — built for agent-cli's trade log format.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TradeRecord:
    """Single trade from trades.jsonl."""
    tick: int = 0
    oid: str = ""
    instrument: str = ""
    side: str = ""           # "buy" or "sell"
    price: float = 0.0
    quantity: float = 0.0
    timestamp_ms: int = 0
    fee: float = 0.0
    strategy: str = ""
    meta: str = ""           # e.g. "guard_close"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeRecord":
        return cls(
            tick=int(d.get("tick", 0)),
            oid=str(d.get("oid", "")),
            instrument=str(d.get("instrument", "")),
            side=str(d.get("side", "")),
            price=float(d.get("price", 0)),
            quantity=float(d.get("quantity", 0)),
            timestamp_ms=int(d.get("timestamp_ms", 0)),
            fee=float(d.get("fee", 0)),
            strategy=str(d.get("strategy", "")),
            meta=str(d.get("meta", "")),
        )


@dataclass
class RoundTrip:
    """A matched entry+exit pair (FIFO)."""
    instrument: str = ""
    direction: str = ""      # "long" or "short"
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: float = 0.0
    entry_ts: int = 0
    exit_ts: int = 0
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    strategy: str = ""
    exit_meta: str = ""

    @property
    def gross_pnl(self) -> float:
        if self.direction == "long":
            return (self.exit_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.exit_price) * self.quantity

    @property
    def total_fees(self) -> float:
        return self.entry_fee + self.exit_fee

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.total_fees

    @property
    def holding_ms(self) -> int:
        return self.exit_ts - self.entry_ts

    @property
    def is_winner(self) -> bool:
        return self.net_pnl > 0


# Holding period bucket boundaries (ms)
_BUCKET_BOUNDS = [
    (5 * 60 * 1000, "<5m"),
    (15 * 60 * 1000, "5-15m"),
    (60 * 60 * 1000, "15-60m"),
    (4 * 60 * 60 * 1000, "1-4h"),
]


def _holding_bucket(ms: int) -> str:
    for bound, label in _BUCKET_BOUNDS:
        if ms < bound:
            return label
    return "4h+"


@dataclass
class ReflectMetrics:
    """All metrics computed by the REFLECT engine."""
    # Core
    total_trades: int = 0
    total_round_trips: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0

    # PnL
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    gross_profit_factor: float = 0.0   # gross_wins / gross_losses
    net_profit_factor: float = 0.0     # net_wins / net_losses

    # Fee analysis
    fdr: float = 0.0  # Fee Drag Ratio = total_fees / gross_wins * 100

    # Direction
    long_count: int = 0
    long_wins: int = 0
    long_pnl: float = 0.0
    short_count: int = 0
    short_wins: int = 0
    short_pnl: float = 0.0

    # Holding periods
    holding_buckets: Dict[str, int] = field(default_factory=dict)
    avg_holding_ms: float = 0.0

    # Streaks
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # Monster trade dependency
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    monster_dependency_pct: float = 0.0  # best_trade_pnl / net_pnl * 100

    # Strategy breakdown
    strategy_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Exit type breakdown (meta field)
    exit_type_counts: Dict[str, int] = field(default_factory=dict)

    # Round trips (for report detail)
    round_trips: List[RoundTrip] = field(default_factory=list)

    # Recommendations
    recommendations: List[str] = field(default_factory=list)


class ReflectEngine:
    """Pure computation engine — takes trades, produces metrics."""

    def compute(
        self,
        trades: List[TradeRecord],
        wolf_state: Optional[Dict] = None,
    ) -> ReflectMetrics:
        m = ReflectMetrics()
        m.total_trades = len(trades)

        if not trades:
            return m

        # 1. FIFO round-trip pairing
        rts = self._pair_round_trips(trades)
        m.round_trips = rts
        m.total_round_trips = len(rts)

        if not rts:
            m.total_fees = sum(t.fee for t in trades)
            return m

        # 2. Win/loss
        m.win_count = sum(1 for r in rts if r.is_winner)
        m.loss_count = m.total_round_trips - m.win_count
        m.win_rate = m.win_count / m.total_round_trips * 100 if m.total_round_trips else 0

        # 3. PnL
        gross_wins = sum(r.gross_pnl for r in rts if r.gross_pnl > 0)
        gross_losses = abs(sum(r.gross_pnl for r in rts if r.gross_pnl < 0))
        m.gross_pnl = sum(r.gross_pnl for r in rts)
        m.total_fees = sum(r.total_fees for r in rts)
        m.net_pnl = sum(r.net_pnl for r in rts)

        m.gross_profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

        net_wins = sum(r.net_pnl for r in rts if r.net_pnl > 0)
        net_losses = abs(sum(r.net_pnl for r in rts if r.net_pnl < 0))
        m.net_profit_factor = net_wins / net_losses if net_losses > 0 else float('inf')

        # 4. FDR
        m.fdr = (m.total_fees / gross_wins * 100) if gross_wins > 0 else 0

        # 5. Direction analysis
        for r in rts:
            if r.direction == "long":
                m.long_count += 1
                m.long_pnl += r.net_pnl
                if r.is_winner:
                    m.long_wins += 1
            else:
                m.short_count += 1
                m.short_pnl += r.net_pnl
                if r.is_winner:
                    m.short_wins += 1

        # 6. Holding period buckets
        buckets: Dict[str, int] = {}
        total_holding = 0
        for r in rts:
            bucket = _holding_bucket(r.holding_ms)
            buckets[bucket] = buckets.get(bucket, 0) + 1
            total_holding += r.holding_ms
        m.holding_buckets = buckets
        m.avg_holding_ms = total_holding / len(rts) if rts else 0

        # 7. Consecutive streaks
        m.max_consecutive_wins, m.max_consecutive_losses = self._compute_streaks(rts)

        # 8. Monster trade dependency
        pnls = [r.net_pnl for r in rts]
        m.best_trade_pnl = max(pnls) if pnls else 0
        m.worst_trade_pnl = min(pnls) if pnls else 0
        if m.net_pnl != 0:
            m.monster_dependency_pct = abs(m.best_trade_pnl / m.net_pnl) * 100
        else:
            m.monster_dependency_pct = 0

        # 9. Strategy breakdown
        m.strategy_stats = self._strategy_breakdown(rts)

        # 10. Exit type breakdown
        exit_counts: Dict[str, int] = {}
        for r in rts:
            key = r.exit_meta or "strategy"
            exit_counts[key] = exit_counts.get(key, 0) + 1
        m.exit_type_counts = exit_counts

        # 11. Recommendations
        m.recommendations = self._generate_recommendations(m)

        return m

    def _pair_round_trips(self, trades: List[TradeRecord]) -> List[RoundTrip]:
        """FIFO matching of buys/sells per instrument to form round trips."""
        # Group by instrument
        by_instrument: Dict[str, List[TradeRecord]] = {}
        for t in trades:
            by_instrument.setdefault(t.instrument, []).append(t)

        round_trips: List[RoundTrip] = []

        for instrument, inst_trades in by_instrument.items():
            # Sort by timestamp
            inst_trades.sort(key=lambda t: t.timestamp_ms)

            # Separate into buys and sells queues
            buys: deque[TradeRecord] = deque()
            sells: deque[TradeRecord] = deque()

            for t in inst_trades:
                if t.side == "buy":
                    buys.append(t)
                else:
                    sells.append(t)

                # Try to match: buy then sell = long RT, sell then buy = short RT
                while buys and sells:
                    buy = buys[0]
                    sell = sells[0]

                    # Determine which came first to get direction
                    if buy.timestamp_ms <= sell.timestamp_ms:
                        # Long: bought first, sold later
                        entry, exit_t = buy, sell
                        direction = "long"
                    else:
                        # Short: sold first, bought back later
                        entry, exit_t = sell, buy
                        direction = "short"

                    match_qty = min(buy.quantity, sell.quantity)
                    if match_qty <= 0:
                        break

                    # Prorate fees by matched quantity
                    buy_fee = buy.fee * (match_qty / buy.quantity) if buy.quantity > 0 else 0
                    sell_fee = sell.fee * (match_qty / sell.quantity) if sell.quantity > 0 else 0

                    rt = RoundTrip(
                        instrument=instrument,
                        direction=direction,
                        entry_price=entry.price,
                        exit_price=exit_t.price,
                        quantity=match_qty,
                        entry_ts=entry.timestamp_ms,
                        exit_ts=exit_t.timestamp_ms,
                        entry_fee=buy_fee if direction == "long" else sell_fee,
                        exit_fee=sell_fee if direction == "long" else buy_fee,
                        strategy=entry.strategy,
                        exit_meta=exit_t.meta,
                    )
                    round_trips.append(rt)

                    # Reduce remaining quantities
                    buy_remaining = buy.quantity - match_qty
                    sell_remaining = sell.quantity - match_qty

                    if buy_remaining <= 1e-12:
                        buys.popleft()
                    else:
                        buys[0] = TradeRecord(
                            tick=buy.tick, oid=buy.oid, instrument=buy.instrument,
                            side=buy.side, price=buy.price, quantity=buy_remaining,
                            timestamp_ms=buy.timestamp_ms,
                            fee=buy.fee - buy_fee,
                            strategy=buy.strategy, meta=buy.meta,
                        )

                    if sell_remaining <= 1e-12:
                        sells.popleft()
                    else:
                        sells[0] = TradeRecord(
                            tick=sell.tick, oid=sell.oid, instrument=sell.instrument,
                            side=sell.side, price=sell.price, quantity=sell_remaining,
                            timestamp_ms=sell.timestamp_ms,
                            fee=sell.fee - sell_fee,
                            strategy=sell.strategy, meta=sell.meta,
                        )

        round_trips.sort(key=lambda r: r.entry_ts)
        return round_trips

    @staticmethod
    def _compute_streaks(rts: List[RoundTrip]) -> Tuple[int, int]:
        """Return (max_consecutive_wins, max_consecutive_losses)."""
        max_wins = max_losses = 0
        cur_wins = cur_losses = 0

        for r in rts:
            if r.is_winner:
                cur_wins += 1
                cur_losses = 0
                max_wins = max(max_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_losses = max(max_losses, cur_losses)

        return max_wins, max_losses

    @staticmethod
    def _strategy_breakdown(rts: List[RoundTrip]) -> Dict[str, Dict[str, Any]]:
        """Per-strategy stats."""
        stats: Dict[str, Dict[str, Any]] = {}

        for r in rts:
            key = r.strategy or "unknown"
            if key not in stats:
                stats[key] = {
                    "count": 0, "wins": 0, "net_pnl": 0.0,
                    "total_fees": 0.0, "gross_pnl": 0.0,
                }
            s = stats[key]
            s["count"] += 1
            if r.is_winner:
                s["wins"] += 1
            s["net_pnl"] += r.net_pnl
            s["gross_pnl"] += r.gross_pnl
            s["total_fees"] += r.total_fees

        # Compute win rates
        for s in stats.values():
            s["win_rate"] = s["wins"] / s["count"] * 100 if s["count"] else 0

        return stats

    @staticmethod
    def _generate_recommendations(m: ReflectMetrics) -> List[str]:
        """Rule-based recommendations from metrics."""
        recs: List[str] = []

        if m.fdr > 30:
            recs.append(
                f"FDR is {m.fdr:.0f}% — fees are eating >30% of gross wins. "
                "Reduce trade frequency or increase edge per trade."
            )
        elif m.fdr > 20:
            recs.append(
                f"FDR is {m.fdr:.0f}% — approaching warning zone. Monitor fee drag."
            )

        if m.win_rate < 40 and m.total_round_trips >= 5:
            recs.append(
                f"Win rate is {m.win_rate:.0f}% — below 40%. Tighten entry criteria."
            )

        if m.monster_dependency_pct > 60 and m.total_round_trips >= 5:
            recs.append(
                f"Monster dependency is {m.monster_dependency_pct:.0f}% — "
                "best trade accounts for >60% of net PnL. Diversify alpha sources."
            )

        if m.max_consecutive_losses >= 5:
            recs.append(
                f"Max consecutive losses: {m.max_consecutive_losses}. "
                "Consider adding a loss-streak circuit breaker."
            )

        if m.long_pnl < 0 and m.short_pnl > 0 and m.long_count >= 3:
            recs.append(
                f"Long PnL: ${m.long_pnl:+.2f}, Short PnL: ${m.short_pnl:+.2f}. "
                "Reduce long bias — short side is more profitable."
            )
        elif m.short_pnl < 0 and m.long_pnl > 0 and m.short_count >= 3:
            recs.append(
                f"Short PnL: ${m.short_pnl:+.2f}, Long PnL: ${m.long_pnl:+.2f}. "
                "Reduce short bias — long side is more profitable."
            )

        if m.total_fees > abs(m.gross_pnl) and m.total_round_trips >= 3:
            recs.append(
                "CRITICAL: Fees exceed gross PnL — every trade is net negative. "
                "Widen spreads or reduce frequency immediately."
            )

        if not recs and m.total_round_trips >= 5:
            recs.append("No major issues detected. Keep current strategy parameters.")

        return recs
