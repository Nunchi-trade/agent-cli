"""Trade journal engine — pure computation, zero I/O.

Structured trade journal with auto-generated reasoning, signal quality
assessment, and nightly review that compares today vs. 7-day rolling average.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.howl_engine import HowlEngine, HowlMetrics, TradeRecord


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class JournalEntry:
    """Structured record of a closed position with reasoning."""
    entry_id: str = ""
    instrument: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    roe_pct: float = 0.0
    holding_ms: int = 0
    entry_source: str = ""
    entry_signal_score: float = 0.0
    close_reason: str = ""
    entry_ts: int = 0
    close_ts: int = 0
    # Auto-generated reasoning
    entry_reasoning: str = ""
    exit_reasoning: str = ""
    signal_quality: str = ""    # "good", "fair", "poor"
    retrospective: str = ""     # what to do differently

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "instrument": self.instrument,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": round(self.pnl, 4),
            "roe_pct": round(self.roe_pct, 2),
            "holding_ms": self.holding_ms,
            "entry_source": self.entry_source,
            "entry_signal_score": self.entry_signal_score,
            "close_reason": self.close_reason,
            "entry_ts": self.entry_ts,
            "close_ts": self.close_ts,
            "entry_reasoning": self.entry_reasoning,
            "exit_reasoning": self.exit_reasoning,
            "signal_quality": self.signal_quality,
            "retrospective": self.retrospective,
        }

    @classmethod
    def from_dict(cls, d: dict) -> JournalEntry:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class NightlyReviewResult:
    """Nightly review comparing today vs. 7-day rolling average."""
    date: str = ""
    round_trips_today: int = 0
    metrics_today: Dict[str, Any] = field(default_factory=dict)
    metrics_7d_avg: Dict[str, Any] = field(default_factory=dict)
    comparison: Dict[str, str] = field(default_factory=dict)
    key_findings: List[str] = field(default_factory=list)
    briefing_md: str = ""


# ---------------------------------------------------------------------------
# Signal quality thresholds
# ---------------------------------------------------------------------------

_QUALITY_THRESHOLDS = {
    "scanner": {"good_score": 200, "fair_score": 170},
    "movers_immediate": {"good_score": 100, "fair_score": 80},
    "movers_signal": {"good_score": 80, "fair_score": 60},
}


# ---------------------------------------------------------------------------
# Pure engine
# ---------------------------------------------------------------------------

class JournalEngine:
    """Pure journal logic. Zero I/O."""

    def create_entry(
        self,
        instrument: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        roe_pct: float,
        entry_source: str,
        entry_signal_score: float,
        close_reason: str,
        entry_ts: int,
        close_ts: int,
    ) -> JournalEntry:
        """Create a structured journal entry from closed position data."""
        holding_ms = close_ts - entry_ts
        entry_id = f"{instrument}-{entry_ts}"

        entry_reasoning = self._generate_entry_reasoning(
            entry_source, entry_signal_score, instrument, direction,
        )
        exit_reasoning = self._generate_exit_reasoning(
            close_reason, roe_pct, holding_ms,
        )
        signal_quality = self._assess_signal_quality(
            entry_source, entry_signal_score, pnl, roe_pct,
        )
        retrospective = self._generate_retrospective(
            signal_quality, entry_source, close_reason, roe_pct, pnl,
        )

        return JournalEntry(
            entry_id=entry_id,
            instrument=instrument,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            roe_pct=roe_pct,
            holding_ms=holding_ms,
            entry_source=entry_source,
            entry_signal_score=entry_signal_score,
            close_reason=close_reason,
            entry_ts=entry_ts,
            close_ts=close_ts,
            entry_reasoning=entry_reasoning,
            exit_reasoning=exit_reasoning,
            signal_quality=signal_quality,
            retrospective=retrospective,
        )

    def compute_nightly_review(
        self,
        today_trades: List[TradeRecord],
        week_trades: List[TradeRecord],
        date: str = "",
    ) -> NightlyReviewResult:
        """Compare today's performance to 7-day rolling average."""
        engine = HowlEngine()

        metrics_today = engine.compute(today_trades)
        metrics_week = engine.compute(week_trades)

        today_dict = self._extract_key_metrics(metrics_today)
        week_dict = self._extract_key_metrics(metrics_week)

        # Compute 7-day daily average (divide by 7)
        avg_dict = {}
        for k, v in week_dict.items():
            if k in ("win_rate", "fdr"):
                avg_dict[k] = v  # Rates don't get divided
            else:
                avg_dict[k] = round(v / 7, 4) if isinstance(v, (int, float)) else v

        comparison = {}
        findings = []

        for k in today_dict:
            t = today_dict.get(k, 0)
            a = avg_dict.get(k, 0)
            if isinstance(t, (int, float)) and isinstance(a, (int, float)) and a != 0:
                pct = ((t - a) / abs(a)) * 100 if a else 0
                if k in ("fdr",):  # Lower is better
                    comparison[k] = "better" if pct < -10 else ("worse" if pct > 10 else "same")
                else:
                    comparison[k] = "better" if pct > 10 else ("worse" if pct < -10 else "same")
            else:
                comparison[k] = "same"

        # Key findings
        if comparison.get("win_rate") == "worse":
            findings.append(f"Win rate below average: {today_dict.get('win_rate', 0):.0f}% vs {avg_dict.get('win_rate', 0):.0f}% avg")
        if comparison.get("net_pnl") == "worse":
            findings.append(f"Net PnL below average: ${today_dict.get('net_pnl', 0):+.2f} vs ${avg_dict.get('net_pnl', 0):+.2f} avg")
        if comparison.get("fdr") == "worse":
            findings.append(f"Fee drag increasing: {today_dict.get('fdr', 0):.0f}% vs {avg_dict.get('fdr', 0):.0f}% avg")
        if comparison.get("win_rate") == "better" and comparison.get("net_pnl") == "better":
            findings.append("Strong day — outperforming 7-day average on both win rate and PnL")
        if not findings:
            findings.append("Performance in line with 7-day average")

        briefing = self._render_briefing(date, today_dict, avg_dict, comparison, findings, metrics_today)

        return NightlyReviewResult(
            date=date,
            round_trips_today=metrics_today.total_round_trips,
            metrics_today=today_dict,
            metrics_7d_avg=avg_dict,
            comparison=comparison,
            key_findings=findings,
            briefing_md=briefing,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_key_metrics(m: HowlMetrics) -> Dict[str, Any]:
        return {
            "round_trips": m.total_round_trips,
            "win_rate": round(m.win_rate, 1),
            "net_pnl": round(m.net_pnl, 4),
            "gross_pnl": round(m.gross_pnl, 4),
            "total_fees": round(m.total_fees, 4),
            "fdr": round(m.fdr, 1),
            "max_consecutive_losses": m.max_consecutive_losses,
        }

    @staticmethod
    def _generate_entry_reasoning(source: str, score: float, instrument: str, direction: str) -> str:
        if source == "movers_immediate":
            return (f"Entered {instrument} {direction} on immediate mover signal "
                    f"(OI breakout + volume surge, confidence {score:.0f})")
        elif source == "scanner":
            return (f"Entered {instrument} {direction} on scanner opportunity "
                    f"(score {score:.0f}/400, above threshold)")
        elif source == "movers_signal":
            return (f"Entered {instrument} {direction} on movers signal "
                    f"(confidence {score:.0f})")
        return f"Entered {instrument} {direction} via {source} (score {score:.0f})"

    @staticmethod
    def _generate_exit_reasoning(close_reason: str, roe_pct: float, holding_ms: int) -> str:
        hours = holding_ms / 3_600_000
        if close_reason == "guard_close":
            return f"Guard trailing stop triggered at {roe_pct:+.1f}% ROE after {hours:.1f}h"
        elif close_reason == "conviction_collapse":
            return f"Signal lost conviction, exited at {roe_pct:+.1f}% ROE after {hours:.1f}h"
        elif close_reason == "stagnation":
            return f"Stagnation take-profit at {roe_pct:+.1f}% ROE — momentum died after {hours:.1f}h"
        elif close_reason == "daily_loss_limit":
            return f"Daily loss limit hit, emergency exit at {roe_pct:+.1f}% ROE"
        elif "hard_stop" in close_reason:
            return f"Hard stop loss at {roe_pct:+.1f}% ROE after {hours:.1f}h"
        return f"Closed: {close_reason} at {roe_pct:+.1f}% ROE after {hours:.1f}h"

    @staticmethod
    def _assess_signal_quality(source: str, score: float, pnl: float, roe_pct: float) -> str:
        thresholds = _QUALITY_THRESHOLDS.get(source, {"good_score": 100, "fair_score": 50})

        if pnl > 0 and score >= thresholds["good_score"]:
            return "good"
        elif pnl > 0:
            return "fair"
        elif pnl <= 0 and score >= thresholds["good_score"]:
            return "poor"  # High confidence signal that lost money
        elif pnl <= 0:
            return "poor"
        return "fair"

    @staticmethod
    def _generate_retrospective(
        quality: str, source: str, close_reason: str, roe_pct: float, pnl: float,
    ) -> str:
        parts = []
        if quality == "poor" and source == "movers_immediate":
            parts.append("Immediate mover signal was a false positive. Consider requiring volume confirmation.")
        elif quality == "poor" and source == "scanner":
            parts.append("High-score scanner entry lost money. Check if macro conditions were unfavorable.")
        elif quality == "poor":
            parts.append(f"Signal from {source} did not translate to profit. Review entry criteria.")

        if close_reason == "conviction_collapse" and roe_pct < -2:
            parts.append("Conviction collapse with significant loss — tighter stop or faster exit needed.")
        elif close_reason == "guard_close" and roe_pct > 5:
            parts.append("Guard captured a good move. Current tier settings are working.")
        elif close_reason == "stagnation" and roe_pct > 0:
            parts.append("Stagnation exit with profit — patience paid off but momentum was limited.")

        if not parts:
            if pnl > 0:
                parts.append("Trade executed as expected. No adjustments needed.")
            else:
                parts.append("Small loss within acceptable range. Monitor for pattern.")

        return " ".join(parts)

    @staticmethod
    def _render_briefing(
        date: str,
        today: Dict,
        avg: Dict,
        comparison: Dict,
        findings: List[str],
        metrics: HowlMetrics,
    ) -> str:
        lines = [
            f"# Nightly Review — {date}",
            "",
            "## Today vs. 7-Day Average",
            "",
            "| Metric | Today | 7d Avg | Trend |",
            "|--------|-------|--------|-------|",
        ]
        labels = {
            "round_trips": "Round Trips",
            "win_rate": "Win Rate",
            "net_pnl": "Net PnL",
            "gross_pnl": "Gross PnL",
            "total_fees": "Fees",
            "fdr": "FDR",
            "max_consecutive_losses": "Max Loss Streak",
        }
        trend_icons = {"better": "^", "worse": "v", "same": "="}
        for k, label in labels.items():
            t = today.get(k, 0)
            a = avg.get(k, 0)
            trend = trend_icons.get(comparison.get(k, "same"), "=")
            if k in ("net_pnl", "gross_pnl", "total_fees"):
                lines.append(f"| {label} | ${t:+.2f} | ${a:+.2f} | {trend} |")
            elif k in ("win_rate", "fdr"):
                lines.append(f"| {label} | {t:.1f}% | {a:.1f}% | {trend} |")
            else:
                lines.append(f"| {label} | {t} | {a} | {trend} |")

        lines.extend(["", "## Key Findings", ""])
        for f in findings:
            lines.append(f"- {f}")

        if metrics.recommendations:
            lines.extend(["", "## Recommendations", ""])
            for r in metrics.recommendations:
                lines.append(f"- {r}")

        return "\n".join(lines)
