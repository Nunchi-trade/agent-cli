"""REFLECT adapter — maps ReflectMetrics to ApexConfig parameter adjustments.

Pure function, zero I/O. Takes metrics + current config, returns a dict
of adjustments with reasons. Applies guardrails to prevent oscillation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from modules.reflect_engine import ReflectMetrics


@dataclass
class Adjustment:
    """A single config parameter adjustment."""
    param: str
    old_value: Any
    new_value: Any
    reason: str


# Guardrail bounds — no parameter goes beyond these
_BOUNDS = {
    "radar_score_threshold": (120, 280),
    "pulse_confidence_threshold": (40.0, 95.0),
    "daily_loss_limit": (50.0, 5000.0),
}


def adapt(
    metrics: ReflectMetrics,
    config,  # ApexConfig — avoid circular import
) -> Tuple[List[Adjustment], str]:
    """Analyze REFLECT metrics and return config adjustments.

    Returns (adjustments, summary_log).
    """
    adjustments: List[Adjustment] = []

    if metrics.total_round_trips < 3:
        return adjustments, "REFLECT: insufficient data (<3 round trips)"

    # 1. CRITICAL: Fees exceed gross PnL — emergency tighten
    if metrics.total_fees > abs(metrics.gross_pnl) and metrics.total_round_trips >= 3:
        adjustments.extend(_emergency_tighten(config))
        return adjustments, "REFLECT: EMERGENCY — fees exceed gross PnL, tightening all entries"

    # 2. FDR > 30% — reduce trade frequency
    if metrics.fdr > 30:
        adj = _clamp_adjust(config, "radar_score_threshold",
                            getattr(config, "radar_score_threshold") + 10,
                            "FDR critical (>30%): raise radar threshold")
        if adj:
            adjustments.append(adj)
        if getattr(config, "pulse_immediate_auto_entry", True):
            adjustments.append(Adjustment(
                param="pulse_immediate_auto_entry",
                old_value=True, new_value=False,
                reason="FDR critical: disable immediate mover entries",
            ))

    # 3. FDR > 20% — moderate warning
    elif metrics.fdr > 20:
        adj = _clamp_adjust(config, "pulse_confidence_threshold",
                            getattr(config, "pulse_confidence_threshold") + 5,
                            "FDR warning (>20%): raise pulse confidence bar")
        if adj:
            adjustments.append(adj)

    # 4. Win rate < 40% — tighten entry criteria
    if metrics.win_rate < 40 and metrics.total_round_trips >= 5:
        adj = _clamp_adjust(config, "radar_score_threshold",
                            getattr(config, "radar_score_threshold") + 10,
                            f"Win rate low ({metrics.win_rate:.0f}%): raise radar threshold")
        if adj:
            adjustments.append(adj)
        adj2 = _clamp_adjust(config, "pulse_confidence_threshold",
                             getattr(config, "pulse_confidence_threshold") + 10,
                             f"Win rate low ({metrics.win_rate:.0f}%): raise pulse confidence")
        if adj2:
            adjustments.append(adj2)

    # 5. Max consecutive losses >= 5 — reduce daily loss limit
    if metrics.max_consecutive_losses >= 5:
        new_limit = getattr(config, "daily_loss_limit") * 0.8
        adj = _clamp_adjust(config, "daily_loss_limit", new_limit,
                            f"Loss streak ({metrics.max_consecutive_losses}): reduce daily limit")
        if adj:
            adjustments.append(adj)

    # 6. Direction imbalance — long losing, short winning (or vice versa)
    if (metrics.long_pnl < 0 and metrics.short_pnl > 0
            and metrics.long_count >= 3):
        cur = getattr(config, "max_same_direction")
        if cur > 1:
            adjustments.append(Adjustment(
                param="max_same_direction",
                old_value=cur, new_value=1,
                reason=f"Long bias losing (${metrics.long_pnl:+.2f}): limit same-direction slots",
            ))

    # 7. Healthy + profitable — relax slightly
    if (not adjustments
            and metrics.win_rate >= 50
            and metrics.net_pnl > 0
            and metrics.fdr < 15
            and metrics.total_round_trips >= 5):
        default_threshold = 170  # ApexConfig default
        cur = getattr(config, "radar_score_threshold")
        if cur > default_threshold:
            adj = _clamp_adjust(config, "radar_score_threshold",
                                cur - 5,
                                "Profitable + healthy: relax radar threshold slightly")
            if adj:
                adjustments.append(adj)

    # Build summary
    if adjustments:
        lines = ["REFLECT auto-adjust:"]
        for a in adjustments:
            lines.append(f"  {a.param}: {a.old_value} -> {a.new_value} ({a.reason})")
        summary = "\n".join(lines)
    else:
        summary = "REFLECT: no adjustments needed"

    return adjustments, summary


def apply_adjustments(adjustments: List[Adjustment], config) -> None:
    """Apply adjustments to an ApexConfig instance in-place."""
    for adj in adjustments:
        setattr(config, adj.param, adj.new_value)


def _clamp_adjust(config, param: str, new_value, reason: str):
    """Create an adjustment with guardrail bounds."""
    old_value = getattr(config, param)
    lo, hi = _BOUNDS.get(param, (None, None))
    if lo is not None:
        new_value = max(lo, new_value)
    if hi is not None:
        new_value = min(hi, new_value)

    # Type consistency
    if isinstance(old_value, int) and isinstance(new_value, float):
        new_value = int(new_value)

    if new_value == old_value:
        return None
    return Adjustment(param=param, old_value=old_value, new_value=new_value, reason=reason)


def suggest_research_directions(metrics: ReflectMetrics) -> List[str]:
    """Convert REFLECT findings into autoresearch exploration hints.

    Returns a list of human-readable search directions that the autoresearch
    skill can use to sweep config parameters.
    """
    directions: List[str] = []

    if metrics.total_round_trips < 3:
        return ["Collect more trades before running autoresearch"]

    # High FDR → tighten radar threshold to filter low-quality entries
    if metrics.fdr > 30:
        directions.append("Try raising radar_score_threshold in [170, 250]")
    elif metrics.fdr > 20:
        directions.append("Try raising radar_score_threshold in [150, 220]")

    # Low win rate → raise pulse confidence bar
    if metrics.win_rate < 40 and metrics.total_round_trips >= 5:
        directions.append("Sweep pulse_confidence_threshold in [70, 95]")

    # Direction imbalance
    if metrics.long_pnl < 0 and metrics.short_pnl > 0 and metrics.long_count >= 3:
        directions.append("Set max_same_direction to 1")
    elif metrics.short_pnl < 0 and metrics.long_pnl > 0 and metrics.short_count >= 3:
        directions.append("Set max_same_direction to 1")

    # Loss streaks → tighten daily loss limit
    if metrics.max_consecutive_losses >= 5:
        directions.append("Reduce daily_loss_limit by 20% from current value")

    # Monster dependency → diversify
    if metrics.monster_dependency_pct > 60 and metrics.total_round_trips >= 5:
        directions.append("Raise radar_score_threshold to reduce reliance on outlier trades")

    # Fees exceed gross PnL → emergency
    if metrics.total_fees > abs(metrics.gross_pnl) and metrics.total_round_trips >= 3:
        directions.append("CRITICAL: Raise radar_score_threshold to [220, 280] and pulse_confidence_threshold to [85, 95]")

    # Healthy — try relaxing
    if (not directions
            and metrics.win_rate >= 50
            and metrics.net_pnl > 0
            and metrics.fdr < 15
            and metrics.total_round_trips >= 5):
        directions.append("Strategy is healthy — try lowering radar_score_threshold in [140, 170] to capture more trades")

    return directions


def _emergency_tighten(config) -> List[Adjustment]:
    """Emergency mode: tighten everything to stop bleeding."""
    adjs = []
    adjs.append(Adjustment(
        param="pulse_immediate_auto_entry",
        old_value=getattr(config, "pulse_immediate_auto_entry"),
        new_value=False,
        reason="EMERGENCY: disable all immediate entries",
    ))
    adj = _clamp_adjust(config, "radar_score_threshold", 250,
                        "EMERGENCY: raise radar threshold to 250")
    if adj:
        adjs.append(adj)
    adj2 = _clamp_adjust(config, "pulse_confidence_threshold", 90.0,
                         "EMERGENCY: raise pulse confidence to 90")
    if adj2:
        adjs.append(adj2)
    return adjs
