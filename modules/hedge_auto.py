"""Agent-controlled automatic hedge opening.

Mirrors the shape of `cli/margin_auto.py` so the safety story is the
same:
  - Pure decision function (`compute_hedge_open_action`)
  - Hard caps on per-action and per-day spend
  - Min-interval guard to prevent rapid-fire
  - State persisted to `~/.nunchi/hedge-auto-state.json`
  - Every decision (action + skip) logged to `~/.nunchi/hedge-auto.log`

Phase 1: auto-OPEN only. The runner watches the user's HL perp
positions for each `policy.coins` entry. When a coin has notional above
the trigger AND no active hedge in `~/.nunchi/hedges.json`, it builds
a CFI v2 proposal and opens the YEX leg at the 1/L ratio.

Phase 2 (auto-unwind on perp close, auto-rebalance on perp resize,
multiple concurrent policies) is intentionally out of scope.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set, Tuple

log = logging.getLogger(__name__)

# Don't burn a signed tx for less than this hedge notional. Builder fee +
# tx latency aren't worth it for crumb-sized hedges.
MIN_HEDGE_NOTIONAL_USD = 10.0


# ─── Policy + action types ──────────────────────────────────────────────────


@dataclass(frozen=True)
class HedgePolicy:
    """Agent's auto-open hedge configuration."""

    notional_trigger_usd: float
    """Open a hedge when the unhedged perp notional exceeds this (USD)."""

    coins: Tuple[str, ...]
    """Coins to watch. Phase 1: only coins with a deployed CFI v2 profile (BTC)."""

    max_hedge_notional_usd: float
    """Hard ceiling on the CFI v2 hedge notional for any single open action."""

    max_per_day_actions: int
    """Hard ceiling on hedge opens per UTC day."""

    min_interval_seconds: int
    """Minimum seconds between successful opens. Prevents rapid-fire."""

    def validate(self) -> None:
        if self.notional_trigger_usd <= 0:
            raise ValueError(
                f"notional_trigger_usd must be > 0, got {self.notional_trigger_usd}",
            )
        if not self.coins:
            raise ValueError("policy.coins must not be empty")
        if self.max_hedge_notional_usd <= 0:
            raise ValueError(
                f"max_hedge_notional_usd must be > 0, got {self.max_hedge_notional_usd}",
            )
        if self.max_per_day_actions <= 0:
            raise ValueError(
                f"max_per_day_actions must be > 0, got {self.max_per_day_actions}",
            )
        if self.min_interval_seconds < 0:
            raise ValueError(
                f"min_interval_seconds must be ≥ 0, got {self.min_interval_seconds}",
            )


@dataclass(frozen=True)
class HedgeOpenAction:
    """The result of a decision: what to open now."""

    coin: str
    perp_notional_usd: float
    """Existing HL perp notional for the coin (sum across cross + isolated)."""
    hedge_notional_usd: float
    """CFI v2 leg notional (≤ perp_notional / L), post-caps."""
    reason: str


@dataclass
class DailyHedgeState:
    """Persisted per-day counters for hedge auto-open.

    UTC-anchored. `reset_if_new_day(today_iso)` rolls counters at midnight.
    """

    date_iso: str
    actions_today: int = 0
    last_action_at_ms: int = 0
    last_action_coin: str = ""
    last_action_hedge_notional_usd: float = 0.0

    @classmethod
    def fresh(cls, today_iso: str) -> "DailyHedgeState":
        return cls(date_iso=today_iso)

    def reset_if_new_day(self, today_iso: str) -> "DailyHedgeState":
        if self.date_iso != today_iso:
            return DailyHedgeState.fresh(today_iso)
        return self

    def record(self, action: HedgeOpenAction, now_ms: int) -> "DailyHedgeState":
        return DailyHedgeState(
            date_iso=self.date_iso,
            actions_today=self.actions_today + 1,
            last_action_at_ms=now_ms,
            last_action_coin=action.coin,
            last_action_hedge_notional_usd=action.hedge_notional_usd,
        )

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, blob: dict) -> "DailyHedgeState":
        return cls(
            date_iso=str(blob.get("date_iso", today_utc_iso())),
            actions_today=int(blob.get("actions_today", 0) or 0),
            last_action_at_ms=int(blob.get("last_action_at_ms", 0) or 0),
            last_action_coin=str(blob.get("last_action_coin", "") or ""),
            last_action_hedge_notional_usd=float(
                blob.get("last_action_hedge_notional_usd", 0.0) or 0.0,
            ),
        )


def today_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── Pure decision function ─────────────────────────────────────────────────


def compute_hedge_open_action(
    *,
    coin: str,
    perp_notional_usd: float,
    active_hedge_coins: Set[str],
    profile_vol_mult_l: int,
    policy: HedgePolicy,
    daily: DailyHedgeState,
    now_ms: int,
) -> Tuple[Optional[HedgeOpenAction], str]:
    """Decide whether to open a hedge for `coin`.

    Returns `(action_or_None, reason)`. The reason string is set for both
    branches so the loop runner can write to the audit log on no-op cycles.
    """
    # 1. Coin in scope?
    if coin not in policy.coins:
        return None, f"skip: {coin} not in policy.coins"

    # 2. Already hedged?
    if coin in active_hedge_coins:
        return None, f"skip: {coin} already has an active hedge"

    # 3. Notional above the open threshold?
    if perp_notional_usd <= 0:
        return None, f"skip: {coin} has no open perp position"
    if perp_notional_usd < policy.notional_trigger_usd:
        return None, (
            f"skip: {coin} notional ${perp_notional_usd:,.0f} < "
            f"trigger ${policy.notional_trigger_usd:,.0f}"
        )

    # 4. Daily cap exhausted?
    if daily.actions_today >= policy.max_per_day_actions:
        return None, (
            f"skip: daily action cap exhausted "
            f"({daily.actions_today}/{policy.max_per_day_actions})"
        )

    # 5. Min-interval guard
    if daily.last_action_at_ms > 0:
        elapsed_s = (now_ms - daily.last_action_at_ms) / 1000.0
        if elapsed_s < policy.min_interval_seconds:
            return None, (
                f"skip: min-interval not met "
                f"({elapsed_s:.0f}s < {policy.min_interval_seconds}s)"
            )

    # 6. Compute hedge notional = perp_notional / L. Cap by per-action ceiling.
    if profile_vol_mult_l <= 0:
        return None, f"skip: invalid L={profile_vol_mult_l} for {coin}"
    hedge_notional = perp_notional_usd / profile_vol_mult_l
    caps_hit = []
    if hedge_notional > policy.max_hedge_notional_usd:
        hedge_notional = policy.max_hedge_notional_usd
        caps_hit.append(f"max-hedge-notional ${policy.max_hedge_notional_usd:,.2f}")

    # 7. Minimum action threshold
    if hedge_notional < MIN_HEDGE_NOTIONAL_USD:
        return None, (
            f"skip: hedge notional ${hedge_notional:.2f} < "
            f"min-action ${MIN_HEDGE_NOTIONAL_USD:.2f}"
        )

    reason = (
        f"trigger {coin} perp notional ${perp_notional_usd:,.0f} ≥ "
        f"${policy.notional_trigger_usd:,.0f} (no active hedge); "
        f"open ${hedge_notional:,.2f} at 1/{profile_vol_mult_l} ratio"
    )
    if caps_hit:
        reason += " [capped: " + ", ".join(caps_hit) + "]"

    return (
        HedgeOpenAction(
            coin=coin,
            perp_notional_usd=perp_notional_usd,
            hedge_notional_usd=hedge_notional,
            reason=reason,
        ),
        reason,
    )


# ─── State persistence ──────────────────────────────────────────────────────


@dataclass
class HedgeAutoStateStore:
    """JSON-file persistence for daily counters."""

    path: Path

    @classmethod
    def default(cls) -> "HedgeAutoStateStore":
        return cls(path=Path.home() / ".nunchi" / "hedge-auto-state.json")

    def load(self) -> DailyHedgeState:
        if not self.path.exists():
            return DailyHedgeState.fresh(today_utc_iso())
        try:
            data = json.loads(self.path.read_text())
            return DailyHedgeState.from_json(data).reset_if_new_day(today_utc_iso())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("hedge_auto: state load failed (%s); starting fresh", e)
            return DailyHedgeState.fresh(today_utc_iso())

    def save(self, state: DailyHedgeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_json(), indent=2))
        tmp.replace(self.path)


def audit_log_path() -> Path:
    return Path.home() / ".nunchi" / "hedge-auto.log"


def append_audit_log(message: str, *, ts_ms: Optional[int] = None) -> None:
    """Append a timestamped line to the audit log. Best-effort, never raises."""
    path = audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if ts_ms is None:
            ts_ms = int(time.time() * 1000)
        iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        )
        with path.open("a") as f:
            f.write(f"{iso}  {message}\n")
    except OSError as e:
        log.warning("hedge_auto: audit-log write failed: %s", e)


# ─── Helpers for the loop runner ────────────────────────────────────────────


def sum_perp_notional_for_coin(positions: list, coin: str) -> float:
    """Sum absolute notional across all perp positions for a coin.

    HL `state["positions"]` is a merged list (cross + isolated + HIP-3).
    A user can hold the same coin in multiple buckets; for the auto-hedge
    trigger we treat the total exposure as the unhedged notional.
    """
    total = 0.0
    coin_upper = coin.upper()
    for entry in positions:
        pos = entry.get("position", {})
        if pos.get("coin", "").upper() != coin_upper:
            continue
        try:
            notional = abs(float(pos.get("positionValue", 0) or 0))
        except (TypeError, ValueError):
            notional = 0.0
        total += notional
    return total


def active_hedge_coins_from_store(hedges: list) -> Set[str]:
    """Build the set of coins with a currently-active hedge job.

    Reads the same `~/.nunchi/hedges.json` store that `nunchi hedge execute`
    writes to. Only `status == "active"` entries count.
    """
    out: Set[str] = set()
    for h in hedges:
        if h.get("status") != "active":
            continue
        c = h.get("coin")
        if isinstance(c, str) and c:
            out.add(c.upper())
    return out
