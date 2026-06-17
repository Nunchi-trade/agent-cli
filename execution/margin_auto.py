"""Agent-controlled automatic margin top-up.

The decision logic lives here as a pure function (`compute_topup_action`)
separated from the loop runner. This means every safety cap, every
threshold, every short-circuit is unit-testable in isolation — no
HL network, no signing, no clock.

Trigger model (Phase 1: cross-account only):

    utilization = total_margin_used / account_value
    if utilization > policy.util_trigger:
        amount = (total_margin_used / policy.util_target) - account_value
        clamp by max-per-topup, daily-remaining, source floor, min action
        action: usdClassTransfer(amount, to_perp=True)

Safety guarantees (all enforced in `compute_topup_action`):
  - Daily cap: spent_today + amount ≤ max_per_day
  - Per-action cap: amount ≤ max_per_topup
  - Source floor: spot_usdc - amount ≥ min_source_balance
  - Min interval: now - last_action ≥ min_interval_seconds
  - Min action: amount ≥ MIN_ACTION_USD (don't burn a tx for $0.20)
  - Date rollover: spent_today resets at UTC midnight

Phase 2 (isolated positions, sub-DEX routing) is intentionally out of
scope — same decision function shape but more policy branches.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Tuple

log = logging.getLogger(__name__)

# Don't burn a signed tx for less than this. HL's minimum order/transfer
# clearance + builder fee makes sub-dollar moves wasteful.
MIN_ACTION_USD = 0.50


# ─── Policy + action types ──────────────────────────────────────────────────


@dataclass(frozen=True)
class TopupPolicy:
    """Agent's auto-topup configuration.

    Phase 1: cross-account only — source=spot, dest=main-perp. The source
    and dest fields are present for forward-compat (YEX routing in Phase 2).
    """

    util_trigger: float
    """Top up when cross-account utilization exceeds this fraction (e.g. 0.7)."""

    util_target: float
    """Bring utilization back to at most this after the top-up (e.g. 0.5)."""

    max_per_topup_usd: float
    """Hard ceiling on any single top-up amount. Caps runaway behaviour."""

    max_per_day_usd: float
    """Hard ceiling on cumulative top-ups per UTC day."""

    min_interval_seconds: int
    """Minimum seconds between successful top-ups. Prevents rapid-fire."""

    min_source_balance_usd: float
    """Never drain the source below this balance (spot USDC floor)."""

    source: Literal["spot"] = "spot"
    """Source of funds. Phase 1: only `spot`."""

    dest: Literal["perp"] = "perp"
    """Destination. Phase 1: only `perp` (main HL perp account)."""

    def validate(self) -> None:
        if not (0 < self.util_trigger < 2):
            raise ValueError(f"util_trigger must be in (0, 2), got {self.util_trigger}")
        if not (0 < self.util_target < self.util_trigger):
            raise ValueError(
                f"util_target ({self.util_target}) must be in (0, util_trigger={self.util_trigger})",
            )
        if self.max_per_topup_usd <= 0:
            raise ValueError(f"max_per_topup_usd must be > 0, got {self.max_per_topup_usd}")
        if self.max_per_day_usd < self.max_per_topup_usd:
            raise ValueError(
                f"max_per_day_usd ({self.max_per_day_usd}) must be ≥ max_per_topup_usd ({self.max_per_topup_usd})",
            )
        if self.min_interval_seconds < 0:
            raise ValueError(f"min_interval_seconds must be ≥ 0, got {self.min_interval_seconds}")
        if self.min_source_balance_usd < 0:
            raise ValueError(
                f"min_source_balance_usd must be ≥ 0, got {self.min_source_balance_usd}",
            )


@dataclass(frozen=True)
class TopupAction:
    """The result of a decision: what to do right now."""

    amount_usd: float
    source: str
    dest: str
    reason: str
    """Human-readable reason for the audit log."""


@dataclass
class DailyState:
    """Persisted per-day counters.

    UTC-anchored so an agent running across timezones doesn't double-spend
    at midnight. Resets via `reset_if_new_day(today_iso)` before each tick.
    """

    date_iso: str
    spent_today_usd: float = 0.0
    last_action_at_ms: int = 0
    last_action_amount_usd: float = 0.0
    actions_today: int = 0

    @classmethod
    def fresh(cls, today_iso: str) -> "DailyState":
        return cls(date_iso=today_iso, spent_today_usd=0.0, last_action_at_ms=0)

    def reset_if_new_day(self, today_iso: str) -> "DailyState":
        """Return a fresh state for today if the stored date is stale."""
        if self.date_iso != today_iso:
            return DailyState.fresh(today_iso)
        return self

    def record(self, action: TopupAction, now_ms: int) -> "DailyState":
        """Return a new DailyState reflecting a just-submitted action."""
        return DailyState(
            date_iso=self.date_iso,
            spent_today_usd=self.spent_today_usd + action.amount_usd,
            last_action_at_ms=now_ms,
            last_action_amount_usd=action.amount_usd,
            actions_today=self.actions_today + 1,
        )

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, blob: dict) -> "DailyState":
        return cls(
            date_iso=str(blob.get("date_iso", today_utc_iso())),
            spent_today_usd=float(blob.get("spent_today_usd", 0.0) or 0.0),
            last_action_at_ms=int(blob.get("last_action_at_ms", 0) or 0),
            last_action_amount_usd=float(blob.get("last_action_amount_usd", 0.0) or 0.0),
            actions_today=int(blob.get("actions_today", 0) or 0),
        )


def today_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── Pure decision function ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AccountReading:
    """Subset of HL `clearinghouseState` the decision function consumes."""

    account_value_usd: float
    total_margin_used_usd: float
    spot_usdc_usd: float


def compute_topup_action(
    *,
    reading: AccountReading,
    policy: TopupPolicy,
    daily: DailyState,
    now_ms: int,
) -> Tuple[Optional[TopupAction], str]:
    """Decide whether to top up, and by how much.

    Returns `(action_or_None, reason)`. The reason string is always set so
    the loop runner can write to the audit log on no-op cycles too.
    """
    # 1. Sanity: zero account value means brand-new wallet or read failure.
    if reading.account_value_usd <= 0:
        return None, f"skip: account_value=${reading.account_value_usd:.2f} ≤ 0"

    # 2. Trigger: is utilization above the threshold?
    util = reading.total_margin_used_usd / reading.account_value_usd
    if util < policy.util_trigger:
        return None, (
            f"skip: util {util * 100:.2f}% < trigger {policy.util_trigger * 100:.2f}%"
        )

    # 3. Min-interval guard: don't fire faster than the configured floor.
    if daily.last_action_at_ms > 0:
        elapsed_s = (now_ms - daily.last_action_at_ms) / 1000.0
        if elapsed_s < policy.min_interval_seconds:
            return None, (
                f"skip: min-interval not met "
                f"({elapsed_s:.0f}s < {policy.min_interval_seconds}s)"
            )

    # 4. Compute the deposit size that brings util ≤ target.
    #    target = total_margin / (account_value + amount)
    #    ⇒ amount = (total_margin / target) - account_value
    desired_account_value = reading.total_margin_used_usd / policy.util_target
    required = desired_account_value - reading.account_value_usd
    if required <= 0:
        return None, f"skip: no top-up required (already at util {util * 100:.2f}%)"

    amount = required
    caps_hit: list[str] = []

    # 5. Per-action cap.
    if amount > policy.max_per_topup_usd:
        amount = policy.max_per_topup_usd
        caps_hit.append(f"max-per-topup ${policy.max_per_topup_usd:.2f}")

    # 6. Daily cap.
    daily_remaining = policy.max_per_day_usd - daily.spent_today_usd
    if daily_remaining <= 0:
        return None, (
            f"skip: daily cap exhausted "
            f"(spent ${daily.spent_today_usd:.2f} / ${policy.max_per_day_usd:.2f})"
        )
    if amount > daily_remaining:
        amount = daily_remaining
        caps_hit.append(f"daily-remaining ${daily_remaining:.2f}")

    # 7. Source-balance floor.
    spendable = reading.spot_usdc_usd - policy.min_source_balance_usd
    if spendable <= 0:
        return None, (
            f"skip: spot balance ${reading.spot_usdc_usd:.2f} ≤ floor "
            f"${policy.min_source_balance_usd:.2f}"
        )
    if amount > spendable:
        amount = spendable
        caps_hit.append(f"source-floor (spendable ${spendable:.2f})")

    # 8. Minimum action: don't burn a tx for crumbs.
    if amount < MIN_ACTION_USD:
        return None, (
            f"skip: computed amount ${amount:.2f} < min-action ${MIN_ACTION_USD:.2f}"
        )

    reason = (
        f"trigger util={util * 100:.2f}% > {policy.util_trigger * 100:.2f}%; "
        f"target {policy.util_target * 100:.2f}% → deposit ${amount:.2f} "
        f"({policy.source}→{policy.dest})"
    )
    if caps_hit:
        reason += " [capped: " + ", ".join(caps_hit) + "]"

    return (
        TopupAction(
            amount_usd=amount,
            source=policy.source,
            dest=policy.dest,
            reason=reason,
        ),
        reason,
    )


# ─── State persistence ──────────────────────────────────────────────────────


@dataclass
class MarginAutoStateStore:
    """JSON-file persistence for daily counters.

    Default location: `~/.nunchi/margin-auto-state.json`. Matches the
    existing `~/.nunchi/identity.json`, `~/.nunchi/hedges.json`,
    `~/.nunchi/discord_allowlist.json` convention.
    """

    path: Path

    @classmethod
    def default(cls) -> "MarginAutoStateStore":
        return cls(path=Path.home() / ".nunchi" / "margin-auto-state.json")

    def load(self) -> DailyState:
        if not self.path.exists():
            return DailyState.fresh(today_utc_iso())
        try:
            data = json.loads(self.path.read_text())
            return DailyState.from_json(data).reset_if_new_day(today_utc_iso())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("margin_auto: state load failed (%s); starting fresh", e)
            return DailyState.fresh(today_utc_iso())

    def save(self, state: DailyState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state.to_json(), indent=2))
        tmp.replace(self.path)


def audit_log_path() -> Path:
    return Path.home() / ".nunchi" / "margin-auto.log"


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
        log.warning("margin_auto: audit-log write failed: %s", e)
