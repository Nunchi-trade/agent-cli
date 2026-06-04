"""Deterministic fixtures for the APEX/REFLECT proof commands.

These fixtures are the single source of truth for `hl apex proof` and
`hl reflect proof`. They are *fully static* — no randomness, no wall-clock,
no network — so the proof output is byte-stable across runs and machines.

The APEX proof drives the pure ``ApexEngine.evaluate`` decision path; the
REFLECT proof drives the pure ``ReflectEngine.compute`` analysis path. Neither
fixture touches a venue adapter, so no order can ever be placed.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Frozen logical clock for the proof (2025-01-01T00:00:00Z in epoch millis).
# All time-relative engine logic (min-hold, stagnation, cooldown) resolves
# against this fixed value so output never depends on the real clock.
PROOF_NOW_MS = 1_735_689_600_000


def apex_proof_state() -> Dict[str, Any]:
    """A 3-slot APEX state with one losing long position.

    Slot 0 is a long opened at 100.0 that is now deeply underwater, which the
    engine will hard-stop (ROE far below max_negative_roe). Slots 1 and 2 are
    empty and available for entry.
    """
    return {
        "tick_count": 7,
        "start_ts": PROOF_NOW_MS - 600_000,
        "daily_pnl": -12.50,
        "daily_loss_triggered": False,
        "total_trades": 4,
        "total_pnl": 31.40,
        "entry_queue": [],
        "slots": [
            {
                "slot_id": 0,
                "status": "active",
                "instrument": "ETH-PERP",
                "direction": "long",
                "entry_source": "radar",
                "entry_signal_score": 182.0,
                "entry_price": 100.0,
                "entry_size": 5.0,
                "margin_allocated": 50.0,
                "current_price": 99.0,
                "current_roe": 0.0,
                "high_water_roe": 4.0,
                "entry_ts": PROOF_NOW_MS - 3_600_000,
                "last_progress_ts": PROOF_NOW_MS - 3_600_000,
            },
            {"slot_id": 1, "status": "empty"},
            {"slot_id": 2, "status": "empty"},
        ],
    }


def apex_proof_pulse_signals() -> List[Dict[str, Any]]:
    """One immediate-mover pulse signal on a fresh asset (drives an entry)."""
    return [
        {
            "asset": "BTC",
            "direction": "LONG",
            "signal_type": "IMMEDIATE_MOVER",
            "confidence": 92.0,
        }
    ]


def apex_proof_radar_opps() -> List[Dict[str, Any]]:
    """One radar opportunity below threshold (proves the gate rejects it)."""
    return [
        {"asset": "SOL", "direction": "LONG", "final_score": 120.0},
    ]


def apex_proof_slot_prices() -> Dict[int, float]:
    """Current price per slot id — slot 0 underwater vs its 100.0 entry."""
    return {0: 99.0}


def reflect_proof_trades() -> List[Dict[str, Any]]:
    """A fixed trade log: 5 round trips (3 winners, 2 losers) + 1 open entry.

    Tuned so the REFLECT engine exercises its full output surface
    deterministically: a win rate, fee drag, a directional split, an orphan
    (open) position, and at least one parameter recommendation (the ETH legs
    carry heavy fees so the Fee-Drag-Ratio rule fires). All values are static —
    the same metrics are produced on every machine and every run.
    """
    base = PROOF_NOW_MS - 6 * 3_600_000
    h = 3_600_000  # 1h

    def _rt(oid_a, oid_b, inst, e_side, e_px, x_px, qty, ts, strat, fee, x_meta=""):
        """Build an entry+exit pair for one round trip."""
        x_side = "sell" if e_side == "buy" else "buy"
        return [
            {"tick": 0, "oid": oid_a, "instrument": inst, "side": e_side,
             "price": e_px, "quantity": qty, "timestamp_ms": ts, "fee": fee,
             "strategy": strat},
            {"tick": 0, "oid": oid_b, "instrument": inst, "side": x_side,
             "price": x_px, "quantity": qty, "timestamp_ms": ts + h, "fee": fee,
             "strategy": strat, "meta": x_meta},
        ]

    trades: List[Dict[str, Any]] = []
    # RT1 long winner on ETH (heavy fee — drives FDR recommendation)
    trades += _rt("p1", "p2", "ETH-PERP", "buy", 100.0, 104.0, 1.0, base, "proof_mm", 1.20)
    # RT2 long loser on ETH (guard exit)
    trades += _rt("p3", "p4", "ETH-PERP", "buy", 104.0, 103.0, 1.0, base + 2 * h, "proof_mm", 1.20, "guard_close")
    # RT3 long winner on ETH
    trades += _rt("p5", "p6", "ETH-PERP", "buy", 103.0, 105.0, 1.0, base + 4 * h, "proof_mm", 1.20)
    # RT4 short winner on BTC
    trades += _rt("p7", "p8", "BTC-PERP", "sell", 50000.0, 49000.0, 0.01, base + h, "proof_mom", 0.25)
    # RT5 short loser on BTC (guard exit)
    trades += _rt("p9", "p10", "BTC-PERP", "sell", 49000.0, 49500.0, 0.01, base + 3 * h, "proof_mom", 0.25, "guard_close")
    # Open entry with no matched exit (orphan / live position)
    trades.append(
        {"tick": 0, "oid": "p11", "instrument": "SOL-PERP", "side": "buy",
         "price": 150.0, "quantity": 2.0, "timestamp_ms": base + 5 * h, "fee": 0.05,
         "strategy": "proof_mom"}
    )
    return trades
