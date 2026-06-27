"""Risk management — House Liquidity Risk Framework enforcement.

Deterministic policy limits: position caps, daily drawdown, circuit breakers,
reduce-only mode, and graduated Risk Guardian gate machine.
No ML-driven decisions (per KorAI spec).
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from parent.position_tracker import PositionTracker

log = logging.getLogger("risk_manager")
ZERO = Decimal("0")


class RiskGate(enum.Enum):
    """3-state gate machine for graduated risk control.

    OPEN      — Normal trading.
    COOLDOWN  — Exits allowed, new entries blocked.
    CLOSED    — All trading halted. Exchange SLs remain.
    """
    OPEN = "OPEN"
    COOLDOWN = "COOLDOWN"
    CLOSED = "CLOSED"


@dataclass
class RiskLimits:
    """Deterministic policy limits from House Liquidity Risk Framework.

    Testnet-scale defaults — use mainnet_defaults() for production.
    """
    max_position_qty: Decimal = Decimal("10.0")       # max ETH per instrument
    max_notional_usd: Decimal = Decimal("25000")      # max notional exposure
    max_order_size: Decimal = Decimal("5.0")           # max single order size
    max_daily_drawdown_pct: Decimal = Decimal("2.5")   # 2.5% daily drawdown limit
    max_leverage: Decimal = Decimal("3.0")             # max leverage
    tvl: Decimal = Decimal("100000")                   # total value locked
    reserve_factor_pct: Decimal = Decimal("10")        # 10% insurance fund

    @classmethod
    def mainnet_defaults(cls) -> "RiskLimits":
        """Conservative mainnet defaults — override via config for production."""
        return cls(
            max_position_qty=Decimal("2.0"),        # 2 ETH max per instrument
            max_notional_usd=Decimal("10000"),       # $10k max notional
            max_order_size=Decimal("1.0"),            # 1 ETH max single order
            max_daily_drawdown_pct=Decimal("1.0"),   # 1% daily drawdown
            max_leverage=Decimal("2.0"),              # 2x max leverage
            tvl=Decimal("50000"),                     # $50k TVL assumption
            reserve_factor_pct=Decimal("20"),         # 20% insurance fund
        )

    @property
    def reserve_amount(self) -> Decimal:
        return self.tvl * self.reserve_factor_pct / Decimal("100")

    @property
    def trading_capital(self) -> Decimal:
        return self.tvl - self.reserve_amount

    @property
    def max_daily_drawdown_abs(self) -> Decimal:
        return self.tvl * self.max_daily_drawdown_pct / Decimal("100")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_position_qty": str(self.max_position_qty),
            "max_notional_usd": str(self.max_notional_usd),
            "max_order_size": str(self.max_order_size),
            "max_daily_drawdown_pct": str(self.max_daily_drawdown_pct),
            "max_leverage": str(self.max_leverage),
            "tvl": str(self.tvl),
            "reserve_factor_pct": str(self.reserve_factor_pct),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskLimits":
        return cls(**{k: Decimal(v) for k, v in data.items()})


@dataclass
class RiskState:
    """Mutable risk state tracked across rounds."""
    daily_pnl: Decimal = ZERO
    daily_high_water: Decimal = ZERO
    daily_drawdown: Decimal = ZERO
    day_start_ms: int = 0
    safe_mode: bool = False
    reduce_only: bool = False
    safe_mode_reason: str = ""
    rounds_in_safe_mode: int = 0
    # Risk Guardian gate machine
    risk_gate: RiskGate = RiskGate.OPEN
    consecutive_losses: int = 0
    cooldown_entered_ts: int = 0
    # Per-wallet blocked state (wallet_id → reason)
    blocked_wallets: Dict[str, str] = field(default_factory=dict)
    # Price history for circuit breaker detection
    price_history: Dict[str, List[Tuple[int, str]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daily_pnl": str(self.daily_pnl),
            "daily_high_water": str(self.daily_high_water),
            "daily_drawdown": str(self.daily_drawdown),
            "day_start_ms": self.day_start_ms,
            "safe_mode": self.safe_mode,
            "reduce_only": self.reduce_only,
            "safe_mode_reason": self.safe_mode_reason,
            "rounds_in_safe_mode": self.rounds_in_safe_mode,
            "risk_gate": self.risk_gate.value,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_entered_ts": self.cooldown_entered_ts,
            "blocked_wallets": self.blocked_wallets,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskState":
        gate_val = data.get("risk_gate", "OPEN")
        risk_gate = RiskGate(gate_val) if isinstance(gate_val, str) else RiskGate.OPEN
        return cls(
            daily_pnl=Decimal(data.get("daily_pnl", "0")),
            daily_high_water=Decimal(data.get("daily_high_water", "0")),
            daily_drawdown=Decimal(data.get("daily_drawdown", "0")),
            day_start_ms=data.get("day_start_ms", 0),
            safe_mode=data.get("safe_mode", False),
            reduce_only=data.get("reduce_only", False),
            safe_mode_reason=data.get("safe_mode_reason", ""),
            rounds_in_safe_mode=data.get("rounds_in_safe_mode", 0),
            risk_gate=risk_gate,
            consecutive_losses=data.get("consecutive_losses", 0),
            cooldown_entered_ts=data.get("cooldown_entered_ts", 0),
            blocked_wallets=data.get("blocked_wallets", {}),
        )


class RiskManager:
    """Pre-round and post-fill risk enforcement."""

    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.state = RiskState(day_start_ms=int(time.time() * 1000))

    def pre_round_check(self, positions: PositionTracker,
                        mark_prices: Dict[str, Decimal]) -> Tuple[bool, str]:
        """Check if we should proceed with this round.

        Returns (ok, reason).
        """
        # Reset daily counters at day boundary
        self._maybe_reset_daily()

        # Safe mode gate
        if self.state.safe_mode:
            self.state.rounds_in_safe_mode += 1
            return False, f"Safe mode active: {self.state.safe_mode_reason}"

        # Daily drawdown check
        if self.state.daily_drawdown >= self.limits.max_daily_drawdown_abs:
            self.state.safe_mode = True
            self.state.safe_mode_reason = "daily_drawdown_breach"
            log.critical("SAFE MODE: daily drawdown %.2f >= limit %.2f",
                         self.state.daily_drawdown, self.limits.max_daily_drawdown_abs)
            return False, (f"Daily drawdown {self.state.daily_drawdown} "
                           f">= limit {self.limits.max_daily_drawdown_abs}")

        # Circuit breaker check per instrument
        for inst, price in mark_prices.items():
            if self._detect_circuit_breaker(inst, price):
                self.state.safe_mode = True
                self.state.safe_mode_reason = f"circuit_breaker_{inst}"
                return False, f"Circuit breaker triggered for {inst}"

        # Leverage check
        total_notional = sum(
            abs(pos.net_qty) * mark_prices.get(inst, pos.avg_entry_price)
            for inst, pos in positions.house_positions.items()
        )
        if self.limits.trading_capital > ZERO:
            leverage = total_notional / self.limits.trading_capital
            if leverage > self.limits.max_leverage:
                log.warning("Leverage %.2f > max %.2f — reduce-only",
                            leverage, self.limits.max_leverage)
                self.state.reduce_only = True

        return True, "ok"

    def post_fill_update(self, positions: PositionTracker,
                         mark_prices: Dict[str, Decimal]) -> None:
        """Update risk state after fills are applied."""
        # Compute total PnL across all instruments
        total_pnl = ZERO
        for inst, pos in positions.house_positions.items():
            mp = mark_prices.get(inst, pos.avg_entry_price)
            total_pnl += pos.total_pnl(mp)

        self.state.daily_pnl = total_pnl
        self.state.daily_high_water = max(self.state.daily_high_water, total_pnl)
        self.state.daily_drawdown = self.state.daily_high_water - total_pnl

        # Check reduce-only thresholds per instrument
        reduce_only = False
        for inst, pos in positions.house_positions.items():
            if abs(pos.net_qty) >= self.limits.max_position_qty:
                log.warning("Position limit reached for %s: qty=%s >= max=%s",
                            inst, pos.net_qty, self.limits.max_position_qty)
                reduce_only = True
            mp = mark_prices.get(inst, pos.avg_entry_price)
            if abs(pos.net_qty * mp) >= self.limits.max_notional_usd:
                log.warning("Notional limit reached for %s: $%s >= max=$%s",
                            inst, abs(pos.net_qty * mp), self.limits.max_notional_usd)
                reduce_only = True
        self.state.reduce_only = reduce_only

        log.info("Risk: pnl=%s drawdown=%s reduce_only=%s safe=%s",
                 self.state.daily_pnl, self.state.daily_drawdown,
                 self.state.reduce_only, self.state.safe_mode)

    def check_reduce_only(self, instrument: str,
                          positions: PositionTracker) -> bool:
        """Check if instrument is in reduce-only mode."""
        if self.state.reduce_only:
            return True
        pos = positions.get_house_position(instrument)
        if abs(pos.net_qty) >= self.limits.max_position_qty:
            return True
        return False

    def validate_orders(self, orders: List[Dict], instrument: str,
                        positions: PositionTracker) -> List[Dict]:
        """Filter orders that violate risk limits. Returns valid orders."""
        valid = []
        for order in orders:
            order_instrument = order.get("instrument") or instrument
            pos = positions.get_house_position(order_instrument)
            is_reduce_only = self.check_reduce_only(order_instrument, positions)
            qty = Decimal(str(order.get("quantity", order.get("size", "0"))))
            side = order.get("side", "")

            # Max order size check
            if qty > self.limits.max_order_size:
                log.warning("Order rejected: size %s > max %s", qty,
                            self.limits.max_order_size)
                continue

            # Reduce-only check
            if is_reduce_only:
                if pos.net_qty > ZERO and side == "buy":
                    log.info("Order rejected: reduce-only, cannot buy when long")
                    continue
                if pos.net_qty < ZERO and side == "sell":
                    log.info("Order rejected: reduce-only, cannot sell when short")
                    continue
                if pos.net_qty == ZERO:
                    log.info("Order rejected: reduce-only, position flat")
                    continue

            valid.append(order)
        return valid

    # ── Risk Guardian Gate Machine ──────────────────────────────────

    def _enter_cooldown(self, now_ms: int, reason: str) -> None:
        """Transition to COOLDOWN state."""
        self.state.risk_gate = RiskGate.COOLDOWN
        self.state.cooldown_entered_ts = now_ms
        log.warning("RISK GATE → COOLDOWN: %s", reason)

    def _enter_closed(self, reason: str) -> None:
        """Transition to CLOSED state."""
        self.state.risk_gate = RiskGate.CLOSED
        self.state.safe_mode = True
        self.state.safe_mode_reason = reason
        log.critical("RISK GATE → CLOSED: %s", reason)

    def record_loss(self, now_ms: Optional[int] = None) -> None:
        """Record a losing trade.  Increments consecutive loss counter and
        may escalate the gate: OPEN → COOLDOWN → CLOSED."""
        if now_ms is None:
            now_ms = int(time.time() * 1000)

        self.state.consecutive_losses += 1
        threshold = getattr(self, "_cooldown_trigger_losses", 2)

        if self.state.risk_gate == RiskGate.OPEN:
            if self.state.consecutive_losses >= threshold:
                self._enter_cooldown(now_ms, f"{self.state.consecutive_losses} consecutive losses")
        elif self.state.risk_gate == RiskGate.COOLDOWN:
            # Already in cooldown and another trigger → escalate to CLOSED
            self._enter_closed("loss_during_cooldown")

    def record_win(self) -> None:
        """Record a winning trade.  Resets the consecutive loss counter."""
        self.state.consecutive_losses = 0

    def check_drawdown(self, current_drawdown: float, limit: float) -> None:
        """If drawdown >= cooldown_drawdown_pct% of limit → COOLDOWN.
        Called externally or from post_fill_update."""
        pct = getattr(self, "_cooldown_drawdown_pct", 50.0)
        if limit > 0 and current_drawdown >= limit * pct / 100.0:
            if self.state.risk_gate == RiskGate.OPEN:
                now_ms = int(time.time() * 1000)
                self._enter_cooldown(now_ms, f"drawdown {current_drawdown:.2f} >= {pct}% of limit {limit:.2f}")
            elif self.state.risk_gate == RiskGate.COOLDOWN:
                self._enter_closed("drawdown_during_cooldown")

    def check_daily_loss(self, daily_loss: float, limit: float) -> None:
        """If daily loss exceeds limit → CLOSED."""
        if limit > 0 and daily_loss >= limit:
            if self.state.risk_gate != RiskGate.CLOSED:
                self._enter_closed(f"daily_loss {daily_loss:.2f} >= limit {limit:.2f}")

    def check_auto_expiry(self, now_ms: Optional[int] = None) -> None:
        """If COOLDOWN and duration elapsed → back to OPEN."""
        if self.state.risk_gate != RiskGate.COOLDOWN:
            return
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        duration = getattr(self, "_cooldown_duration_ms", 1_800_000)
        if now_ms - self.state.cooldown_entered_ts >= duration:
            self.state.risk_gate = RiskGate.OPEN
            self.state.consecutive_losses = 0
            log.info("RISK GATE → OPEN: cooldown auto-expired")

    def daily_reset(self) -> None:
        """Reset gate to OPEN and clear counters (called at day boundary)."""
        self.state.risk_gate = RiskGate.OPEN
        self.state.consecutive_losses = 0
        self.state.cooldown_entered_ts = 0
        log.info("RISK GATE → OPEN: daily reset")

    def can_open_position(self) -> bool:
        """True only if gate is OPEN — new entries allowed."""
        return self.state.risk_gate == RiskGate.OPEN

    def can_trade(self) -> bool:
        """True if OPEN or COOLDOWN (exits still allowed in COOLDOWN)."""
        return self.state.risk_gate in (RiskGate.OPEN, RiskGate.COOLDOWN)

    def configure_gate(self, *, cooldown_duration_ms: int = 1_800_000,
                       cooldown_trigger_losses: int = 2,
                       cooldown_drawdown_pct: float = 50.0) -> None:
        """Apply gate configuration (typically from ApexConfig)."""
        self._cooldown_duration_ms = cooldown_duration_ms
        self._cooldown_trigger_losses = cooldown_trigger_losses
        self._cooldown_drawdown_pct = cooldown_drawdown_pct

    def clear_safe_mode(self) -> None:
        """Manually clear safe mode (e.g., operator override)."""
        log.info("Safe mode cleared manually")
        self.state.safe_mode = False
        self.state.safe_mode_reason = ""
        self.state.rounds_in_safe_mode = 0
        self.state.reduce_only = False

    def _detect_circuit_breaker(self, instrument: str, price: Decimal) -> bool:
        """Check for 50% price drop in 60 seconds (Black Swan detector)."""
        now_ms = int(time.time() * 1000)
        history = self.state.price_history.setdefault(instrument, [])
        history.append((now_ms, str(price)))

        # Keep only last 120 seconds
        cutoff = now_ms - 120_000
        self.state.price_history[instrument] = [
            (t, p) for t, p in history if t >= cutoff
        ]

        # Check price one minute ago
        one_min_ago = now_ms - 60_000
        old_prices = [
            Decimal(p) for t, p in self.state.price_history[instrument]
            if t <= one_min_ago
        ]
        if old_prices and price > ZERO:
            ref = old_prices[0]
            if ref > ZERO and (ref - price) / ref > Decimal("0.5"):
                log.critical("CIRCUIT BREAKER: %s dropped >50%% in 1min: %s -> %s",
                             instrument, ref, price)
                return True
        return False

    def _maybe_reset_daily(self) -> None:
        """Reset daily counters at day boundary."""
        now_ms = int(time.time() * 1000)
        day_ms = 86_400_000
        if now_ms - self.state.day_start_ms >= day_ms:
            log.info("Daily risk counters reset")
            self.state.daily_pnl = ZERO
            self.state.daily_high_water = ZERO
            self.state.daily_drawdown = ZERO
            self.state.day_start_ms = now_ms
            if self.state.safe_mode and self.state.safe_mode_reason == "daily_drawdown_breach":
                self.state.safe_mode = False
                self.state.safe_mode_reason = ""
                self.state.rounds_in_safe_mode = 0
            self.state.reduce_only = False
            self.clear_wallet_blocks()

    # ── Per-Wallet Risk ──────────────────────────────────────────────

    def check_wallet_daily_loss(self, wallet_id: str, wallet_pnl: float,
                                wallet_limit: float) -> bool:
        """Check if a specific wallet has breached its daily loss limit.

        Returns True if the wallet should stop trading (loss >= limit).
        Persists block in state.blocked_wallets for observability.
        Does NOT change the house-level gate — that's separate.
        """
        if wallet_limit <= 0:
            # Unblock if previously blocked (limit changed)
            self.state.blocked_wallets.pop(wallet_id, None)
            return False
        if wallet_pnl <= -wallet_limit:
            if wallet_id not in self.state.blocked_wallets:
                log.warning("Wallet %s daily loss %.2f >= limit %.2f — blocking entries",
                            wallet_id, abs(wallet_pnl), wallet_limit)
            self.state.blocked_wallets[wallet_id] = f"daily_loss_{abs(wallet_pnl):.0f}"
            return True
        # Clear block if PnL recovered
        self.state.blocked_wallets.pop(wallet_id, None)
        return False

    def clear_wallet_blocks(self) -> None:
        """Clear all per-wallet blocks (called at daily reset)."""
        if self.state.blocked_wallets:
            log.info("Cleared %d wallet blocks", len(self.state.blocked_wallets))
        self.state.blocked_wallets.clear()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limits": self.limits.to_dict(),
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskManager":
        limits = RiskLimits.from_dict(data.get("limits", {})) if data.get("limits") else RiskLimits()
        rm = cls(limits=limits)
        if data.get("state"):
            rm.state = RiskState.from_dict(data["state"])
        return rm
