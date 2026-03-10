"""WolfEngine — pure decision engine for multi-slot trading (zero I/O).

Given state + signals + prices, returns a list of actions to execute.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from modules.wolf_config import WolfConfig
from modules.wolf_state import WolfSlot, WolfState


@dataclass
class WolfAction:
    """A single action the WOLF runner should execute."""
    action: str             # "enter", "exit", "noop"
    slot_id: int = -1
    instrument: str = ""
    direction: str = ""     # "long" or "short"
    size: float = 0.0
    reason: str = ""
    source: str = ""        # movers_immediate, movers_signal, radar
    signal_score: float = 0.0
    execution_algo: str = "immediate"  # "immediate" or "twap"


class WolfEngine:
    """Stateless WOLF decision engine. Zero I/O."""

    def __init__(self, config: WolfConfig):
        self.config = config

    def evaluate(
        self,
        state: WolfState,
        movers_signals: List[Dict[str, Any]],
        scanner_opps: List[Dict[str, Any]],
        slot_prices: Dict[int, float],
        slot_dsl_results: Dict[int, Dict[str, Any]],
        now_ms: int = 0,
        smart_money_signals: Optional[List[Dict[str, Any]]] = None,
    ) -> List[WolfAction]:
        """Evaluate all positions and signals, return ordered actions.

        Priority: risk gate → exits → entries.
        """
        if now_ms == 0:
            now_ms = int(time.time() * 1000)

        actions: List[WolfAction] = []
        cfg = self.config

        # 1. Risk gate: daily loss limit
        if state.daily_pnl <= -cfg.daily_loss_limit or state.daily_loss_triggered:
            for slot in state.active_slots():
                actions.append(WolfAction(
                    action="exit", slot_id=slot.slot_id,
                    instrument=slot.instrument, direction=slot.direction,
                    reason="daily_loss_limit",
                ))
            return actions

        # 2. Exit checks for each active slot
        for slot in state.active_slots():
            exit_action = self._check_exit(
                slot, movers_signals, scanner_opps,
                slot_prices.get(slot.slot_id, 0),
                slot_dsl_results.get(slot.slot_id, {}),
                now_ms,
            )
            if exit_action:
                actions.append(exit_action)

        # 3. Entry evaluation
        entry_actions = self._evaluate_entries(
            state, movers_signals, scanner_opps, now_ms,
            smart_money_signals=smart_money_signals or [],
        )
        actions.extend(entry_actions)

        return actions

    def _check_exit(
        self,
        slot: WolfSlot,
        movers_signals: List[Dict],
        scanner_opps: List[Dict],
        current_price: float,
        dsl_result: Dict,
        now_ms: int,
    ) -> Optional[WolfAction]:
        """Check exit conditions for one active slot."""
        cfg = self.config

        # Update ROE from current price
        if current_price > 0 and slot.entry_price > 0:
            if slot.direction == "long":
                slot.current_roe = (current_price - slot.entry_price) / slot.entry_price * cfg.leverage * 100
            else:
                slot.current_roe = (slot.entry_price - current_price) / slot.entry_price * cfg.leverage * 100
            slot.current_price = current_price

            if slot.current_roe > slot.high_water_roe:
                slot.high_water_roe = slot.current_roe
                slot.last_progress_ts = now_ms

        # 1. DSL close
        if dsl_result.get("action") == "close":
            return WolfAction(
                action="exit", slot_id=slot.slot_id,
                instrument=slot.instrument, direction=slot.direction,
                reason=f"guard_close: {dsl_result.get('reason', '')}",
            )

        # 2. Hard stop
        if slot.current_roe <= cfg.max_negative_roe:
            return WolfAction(
                action="exit", slot_id=slot.slot_id,
                instrument=slot.instrument, direction=slot.direction,
                reason=f"hard_stop: ROE {slot.current_roe:.1f}%",
            )

        # 3. Conviction collapse
        coin = slot.instrument.replace("-PERP", "")
        still_in_signals = any(
            s.get("asset") == coin for s in movers_signals
        )
        still_in_scanner = any(
            o.get("asset") == coin and o.get("direction", "").lower() == slot.direction
            for o in scanner_opps
        )

        if still_in_signals or still_in_scanner:
            slot.last_signal_seen_ts = now_ms
            slot.signal_disappeared_ts = 0
        else:
            if slot.signal_disappeared_ts == 0:
                slot.signal_disappeared_ts = now_ms

            if slot.signal_disappeared_ts > 0 and slot.current_roe < 0:
                elapsed_min = (now_ms - slot.signal_disappeared_ts) / 60_000
                if elapsed_min >= cfg.conviction_collapse_minutes:
                    return WolfAction(
                        action="exit", slot_id=slot.slot_id,
                        instrument=slot.instrument, direction=slot.direction,
                        reason=f"conviction_collapse: {elapsed_min:.0f}min no signal, ROE={slot.current_roe:.1f}%",
                    )

        # 4. Stagnation
        if slot.current_roe >= cfg.stagnation_min_roe and slot.last_progress_ts > 0:
            stagnation_min = (now_ms - slot.last_progress_ts) / 60_000
            if stagnation_min >= cfg.stagnation_minutes:
                return WolfAction(
                    action="exit", slot_id=slot.slot_id,
                    instrument=slot.instrument, direction=slot.direction,
                    reason=f"stagnation_tp: ROE={slot.current_roe:.1f}% stuck for {stagnation_min:.0f}min",
                )

        return None

    def _evaluate_entries(
        self,
        state: WolfState,
        movers_signals: List[Dict],
        scanner_opps: List[Dict],
        now_ms: int,
        smart_money_signals: Optional[List[Dict[str, Any]]] = None,
    ) -> List[WolfAction]:
        """Evaluate potential new entries."""
        cfg = self.config
        actions: List[WolfAction] = []
        active_instruments = state.active_instruments()

        # Collect candidates in priority order
        candidates: List[Dict[str, Any]] = []

        # Priority 1: Movers IMMEDIATE signals
        for sig in movers_signals:
            if sig.get("signal_type") == "IMMEDIATE_MOVER" and cfg.movers_immediate_auto_entry:
                instrument = sig["asset"] + "-PERP"
                if instrument not in active_instruments and instrument not in cfg.excluded_instruments:
                    candidates.append({
                        "instrument": instrument,
                        "direction": sig.get("direction", "LONG").lower(),
                        "source": "movers_immediate",
                        "score": sig.get("confidence", 100),
                        "priority": 1,
                    })

        # Priority 1.5: Smart money signals (HIGH_CONVICTION) / 2.5 (SMART_MONEY)
        for sig in (smart_money_signals or []):
            if sig.get("confidence", 0) >= 60:
                instrument = sig["asset"] + "-PERP"
                if instrument not in active_instruments and instrument not in cfg.excluded_instruments:
                    candidates.append({
                        "instrument": instrument,
                        "direction": sig.get("direction", "LONG").lower(),
                        "source": f"smart_money:{sig.get('signal_type', '')}",
                        "score": sig.get("confidence", 0),
                        "priority": 1.5 if sig.get("signal_type") == "HIGH_CONVICTION" else 2.5,
                    })

        # Priority 2: Radar high scores
        for opp in scanner_opps:
            if opp.get("final_score", 0) >= cfg.radar_score_threshold:
                instrument = opp["asset"] + "-PERP"
                if instrument not in active_instruments and instrument not in cfg.excluded_instruments:
                    candidates.append({
                        "instrument": instrument,
                        "direction": opp.get("direction", "LONG").lower(),
                        "source": "radar",
                        "score": opp.get("final_score", 0),
                        "priority": 2,
                    })

        # Priority 3: Movers other signals
        for sig in movers_signals:
            if sig.get("signal_type") != "IMMEDIATE_MOVER":
                if sig.get("confidence", 0) >= cfg.movers_confidence_threshold:
                    instrument = sig["asset"] + "-PERP"
                    if instrument not in active_instruments and instrument not in cfg.excluded_instruments:
                        candidates.append({
                            "instrument": instrument,
                            "direction": sig.get("direction", "LONG").lower(),
                            "source": "movers_signal",
                            "score": sig.get("confidence", 0),
                            "priority": 3,
                        })

        # Deduplicate by instrument (keep highest priority)
        seen = set()
        unique = []
        for c in candidates:
            if c["instrument"] not in seen:
                seen.add(c["instrument"])
                unique.append(c)
        candidates = unique

        # Sort by priority then score
        candidates.sort(key=lambda c: (c["priority"], -c["score"]))

        # Fill available slots
        for cand in candidates:
            slot = state.get_empty_slot()
            if slot is None:
                break

            # Check direction limit
            if state.direction_count(cand["direction"]) >= cfg.max_same_direction:
                continue

            # Compute size
            margin = cfg.margin_per_slot
            # Size will be computed by runner using current price + leverage

            # Determine execution algo based on notional size
            notional = cfg.margin_per_slot * cfg.leverage
            exec_algo = "twap" if notional > cfg.twap_threshold_usd else "immediate"

            actions.append(WolfAction(
                action="enter",
                slot_id=slot.slot_id,
                instrument=cand["instrument"],
                direction=cand["direction"],
                reason=f"{cand['source']}: score={cand['score']:.0f}",
                source=cand["source"],
                signal_score=cand["score"],
                execution_algo=exec_algo,
            ))

            # Mark slot as taken (so next candidate gets a different slot)
            slot.status = "entering"
            slot.instrument = cand["instrument"]

        return actions
