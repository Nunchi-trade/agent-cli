"""Judge engine — pure computation, zero I/O.

Post-hoc evaluation of WOLF trading decisions. Scores signal quality,
identifies false positives per signal source, evaluates Guard efficiency,
builds playbook stats, and generates config recommendations.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SignalQualityScore:
    """Quality assessment of a single signal that led to a trade."""
    signal_source: str = ""
    instrument: str = ""
    entry_score: float = 0.0      # Original signal score at entry
    outcome_score: float = 0.0    # 0-100 based on actual PnL outcome
    was_accurate: bool = False     # Did signal predict profit?

    def to_dict(self) -> dict:
        return {
            "signal_source": self.signal_source,
            "instrument": self.instrument,
            "entry_score": self.entry_score,
            "outcome_score": round(self.outcome_score, 1),
            "was_accurate": self.was_accurate,
        }


@dataclass
class JudgeFinding:
    """A single evaluation finding."""
    finding_type: str = ""    # signal_quality, false_positive, guard_efficiency,
                              # exit_quality, recommendation
    source: str = ""          # Which signal source
    instrument: str = ""
    detail: str = ""          # Human-readable description
    score: float = 0.0        # Quantified metric
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "finding_type": self.finding_type,
            "source": self.source,
            "instrument": self.instrument,
            "detail": self.detail,
            "score": round(self.score, 2),
            "recommendation": self.recommendation,
        }


@dataclass
class JudgeReport:
    """Complete evaluation report."""
    timestamp_ms: int = 0
    round_trips_evaluated: int = 0
    signal_scores: List[SignalQualityScore] = field(default_factory=list)
    findings: List[JudgeFinding] = field(default_factory=list)
    false_positive_rates: Dict[str, float] = field(default_factory=dict)
    playbook_stats: Dict[str, Dict] = field(default_factory=dict)
    config_recommendations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp_ms": self.timestamp_ms,
            "round_trips_evaluated": self.round_trips_evaluated,
            "signal_scores": [s.to_dict() for s in self.signal_scores],
            "findings": [f.to_dict() for f in self.findings],
            "false_positive_rates": {
                k: round(v, 1) for k, v in self.false_positive_rates.items()
            },
            "playbook_stats": self.playbook_stats,
            "config_recommendations": self.config_recommendations,
        }

    @classmethod
    def from_dict(cls, d: dict) -> JudgeReport:
        return cls(
            timestamp_ms=d.get("timestamp_ms", 0),
            round_trips_evaluated=d.get("round_trips_evaluated", 0),
            signal_scores=[],  # Simplified for read-back
            findings=[JudgeFinding(**f) for f in d.get("findings", [])],
            false_positive_rates=d.get("false_positive_rates", {}),
            playbook_stats=d.get("playbook_stats", {}),
            config_recommendations=d.get("config_recommendations", []),
        )


# ---------------------------------------------------------------------------
# Pure engine
# ---------------------------------------------------------------------------

class JudgeEngine:
    """Evaluates closed round trips for signal quality and trading patterns.

    Works with enriched trade records that contain entry source info in the
    `meta` field (e.g., "entry:movers_immediate", "entry:scanner").
    """

    def evaluate(
        self,
        trades: List[dict],
        closed_slots: Optional[List[dict]] = None,
    ) -> JudgeReport:
        """Main evaluation entry point.

        Args:
            trades: Raw trade record dicts from trades.jsonl
            closed_slots: Optional list of closed WolfSlot dicts with
                          high_water_roe for Guard efficiency analysis
        """
        now_ms = int(time.time() * 1000)

        # Parse entry/exit pairs from trade records
        pairs = self._pair_trades(trades)

        signal_scores = [self._score_signal(p) for p in pairs] if pairs else []
        fp_rates = self._compute_false_positive_rates(pairs) if pairs else {}

        findings = []

        # Per-source findings
        for source, rate in fp_rates.items():
            if rate > 60:
                findings.append(JudgeFinding(
                    finding_type="false_positive",
                    source=source,
                    detail=f"{source} has {rate:.0f}% false positive rate",
                    score=rate,
                    recommendation=f"Tighten {source} entry criteria — majority of entries lose money",
                ))
            elif rate > 40:
                findings.append(JudgeFinding(
                    finding_type="false_positive",
                    source=source,
                    detail=f"{source} has {rate:.0f}% false positive rate (elevated)",
                    score=rate,
                    recommendation=f"Monitor {source} — approaching unreliable territory",
                ))

        # Guard efficiency (if slot data available)
        if closed_slots:
            for slot in closed_slots:
                finding = self._evaluate_guard_efficiency(slot)
                if finding:
                    findings.append(finding)

        # Playbook stats
        playbook_stats = self._build_playbook_stats(pairs)

        # Config recommendations
        config_recs = self._generate_recommendations(fp_rates, pairs)

        return JudgeReport(
            timestamp_ms=now_ms,
            round_trips_evaluated=len(pairs),
            signal_scores=signal_scores,
            findings=findings,
            false_positive_rates=fp_rates,
            playbook_stats=playbook_stats,
            config_recommendations=config_recs,
        )

    # ------------------------------------------------------------------
    # Trade pairing
    # ------------------------------------------------------------------

    @staticmethod
    def _pair_trades(trades: List[dict]) -> List[dict]:
        """Pair entry and exit trades into round trips.

        Each pair is a dict with: instrument, direction, entry_source,
        entry_score, entry_price, exit_price, pnl, roe_pct, holding_ms,
        close_reason.
        """
        # Group by instrument
        by_inst: Dict[str, List[dict]] = {}
        for t in trades:
            inst = t.get("instrument", "")
            by_inst.setdefault(inst, []).append(t)

        pairs = []
        for inst, inst_trades in by_inst.items():
            inst_trades.sort(key=lambda t: t.get("timestamp_ms", 0))

            entries = []
            for t in inst_trades:
                meta = t.get("meta", "")
                if meta.startswith("entry:"):
                    entries.append(t)
                elif entries:
                    # This is an exit — pair with most recent entry
                    entry = entries.pop(0)
                    entry_meta = entry.get("meta", "")
                    source = entry_meta.replace("entry:", "") if entry_meta.startswith("entry:") else "unknown"

                    ep = float(entry.get("price", 0))
                    xp = float(t.get("price", 0))
                    qty = float(entry.get("quantity", 0))

                    # Determine direction from entry side
                    direction = "long" if entry.get("side") == "buy" else "short"
                    if direction == "long":
                        pnl = (xp - ep) * qty
                        roe_pct = ((xp - ep) / ep * 100) if ep else 0
                    else:
                        pnl = (ep - xp) * qty
                        roe_pct = ((ep - xp) / ep * 100) if ep else 0

                    holding = t.get("timestamp_ms", 0) - entry.get("timestamp_ms", 0)

                    pairs.append({
                        "instrument": inst,
                        "direction": direction,
                        "entry_source": source,
                        "entry_score": float(entry.get("entry_signal_score", 0)),
                        "entry_price": ep,
                        "exit_price": xp,
                        "pnl": pnl,
                        "roe_pct": roe_pct,
                        "holding_ms": holding,
                        "close_reason": t.get("meta", ""),
                    })

        return pairs

    # ------------------------------------------------------------------
    # Signal scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _score_signal(pair: dict) -> SignalQualityScore:
        """Score a signal based on entry confidence vs. actual outcome."""
        source = pair["entry_source"]
        pnl = pair["pnl"]
        roe = pair.get("roe_pct", 0)

        # Outcome score: 0-100 based on ROE
        if roe >= 5:
            outcome = min(100, 60 + roe * 2)
        elif roe >= 0:
            outcome = 40 + roe * 4
        elif roe >= -3:
            outcome = max(0, 30 + roe * 10)
        else:
            outcome = 0

        return SignalQualityScore(
            signal_source=source,
            instrument=pair["instrument"],
            entry_score=pair.get("entry_score", 0),
            outcome_score=outcome,
            was_accurate=pnl > 0,
        )

    # ------------------------------------------------------------------
    # False positive analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_false_positive_rates(pairs: List[dict]) -> Dict[str, float]:
        """Compute losing trade rate per signal source."""
        by_source: Dict[str, List[bool]] = {}
        for p in pairs:
            source = p["entry_source"]
            by_source.setdefault(source, []).append(p["pnl"] <= 0)

        rates = {}
        for source, results in by_source.items():
            if len(results) >= 2:  # Need at least 2 trades for meaningful rate
                rates[source] = sum(results) / len(results) * 100
        return rates

    # ------------------------------------------------------------------
    # Guard efficiency
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_guard_efficiency(slot: dict) -> Optional[JudgeFinding]:
        """Evaluate how much of the price move Guard captured."""
        high_water = slot.get("high_water_roe", 0)
        close_roe = slot.get("current_roe", 0)
        close_reason = slot.get("close_reason", "")

        if close_reason != "guard_close" or high_water <= 0:
            return None

        capture_pct = (close_roe / high_water * 100) if high_water else 0

        if capture_pct < 50:
            return JudgeFinding(
                finding_type="guard_efficiency",
                source="guard",
                instrument=slot.get("instrument", ""),
                detail=f"Guard captured only {capture_pct:.0f}% of peak move "
                       f"(exit {close_roe:.1f}% vs peak {high_water:.1f}%)",
                score=capture_pct,
                recommendation="Consider tighter Guard tiers to lock more profit",
            )
        return None

    # ------------------------------------------------------------------
    # Playbook stats
    # ------------------------------------------------------------------

    @staticmethod
    def _build_playbook_stats(pairs: List[dict]) -> Dict[str, Dict]:
        """Build per-instrument, per-source accumulated stats."""
        stats: Dict[str, Dict] = {}
        for p in pairs:
            key = f"{p['instrument']}:{p['entry_source']}"
            if key not in stats:
                stats[key] = {
                    "instrument": p["instrument"],
                    "source": p["entry_source"],
                    "count": 0, "wins": 0, "total_pnl": 0.0,
                    "total_roe": 0.0, "avg_holding_h": 0.0,
                }
            s = stats[key]
            s["count"] += 1
            if p["pnl"] > 0:
                s["wins"] += 1
            s["total_pnl"] += p["pnl"]
            s["total_roe"] += p.get("roe_pct", 0)

        # Compute derived fields
        for s in stats.values():
            n = s["count"]
            s["win_rate"] = round(s["wins"] / n * 100, 1) if n else 0
            s["avg_pnl"] = round(s["total_pnl"] / n, 4) if n else 0
            s["avg_roe"] = round(s["total_roe"] / n, 2) if n else 0
            s["total_pnl"] = round(s["total_pnl"], 4)

        return stats

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_recommendations(
        fp_rates: Dict[str, float],
        pairs: List[dict],
    ) -> List[Dict[str, Any]]:
        """Generate config recommendations based on findings."""
        recs = []

        # Movers immediate: if FP > 50%, recommend disabling auto-entry
        if fp_rates.get("movers_immediate", 0) > 50:
            recs.append({
                "param": "movers_immediate_auto_entry",
                "suggested_value": False,
                "reason": f"Movers immediate FP rate is {fp_rates['movers_immediate']:.0f}% — "
                          "disable auto-entry until signal quality improves",
                "summary": "Disable movers_immediate auto-entry (high FP rate)",
            })

        # Movers signal: if FP > 50%, raise threshold
        if fp_rates.get("movers_signal", 0) > 50:
            recs.append({
                "param": "movers_confidence_threshold",
                "suggested_value": 85.0,
                "reason": f"Movers signal FP rate is {fp_rates['movers_signal']:.0f}% — "
                          "raise confidence threshold to filter weak signals",
                "summary": "Raise movers confidence threshold",
            })

        # Scanner: if FP > 50%, raise threshold
        if fp_rates.get("scanner", 0) > 50:
            recs.append({
                "param": "scanner_score_threshold",
                "suggested_value": 200,
                "reason": f"Scanner FP rate is {fp_rates['scanner']:.0f}% — "
                          "raise score threshold to allow only high-conviction entries",
                "summary": "Raise scanner score threshold",
            })

        # Check for direction imbalance causing losses
        by_dir: Dict[str, List[float]] = {"long": [], "short": []}
        for p in pairs:
            d = p.get("direction", "")
            if d in by_dir:
                by_dir[d].append(p["pnl"])

        for direction in ("long", "short"):
            trades = by_dir[direction]
            if len(trades) >= 3:
                total = sum(trades)
                if total < 0 and sum(by_dir.get("short" if direction == "long" else "long", [])) > 0:
                    recs.append({
                        "param": "direction_bias",
                        "suggested_value": "short" if direction == "long" else "long",
                        "reason": f"{direction} trades losing ${abs(total):.2f} total "
                                  "while opposite direction is profitable",
                        "summary": f"Reduce {direction} exposure",
                    })
                    break  # Only one direction rec

        return recs
