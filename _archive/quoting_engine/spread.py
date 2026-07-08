"""Half-spread calculation with fee, vol, toxicity, and event components.

h = max(0.5 * tick, h_fee + h_vol + h_tox + h_event) - rebate_credit

Then apply risk multipliers:
    h_final = h * sqrt(m_vol) * m_dd

Uses sqrt(m_vol) to dampen the vol multiplier — vol is already priced
into h_vol, so the multiplier is a regime amplifier, not a second vol
factor. Matches the existing AvellanedaStoikovMM convention.
"""
from __future__ import annotations

import math

from quoting_engine.config import SpreadParams


class SpreadCalculator:
    """Compute the half-spread from its additive components."""

    def __init__(self, params: SpreadParams, tick_size: float = 0.01):
        self._p = params
        self._tick_size = tick_size

    def compute(
        self,
        mid: float,
        sigma_price: float,
        m_vol: float = 1.0,
        m_dd: float = 1.0,
        h_tox: float = 0.0,
        h_event: float = 0.0,
    ) -> float:
        """Compute final half-spread in price units.

        Args:
            mid: reference price for bps conversions.
            sigma_price: rolling vol in price units (sigma_log * mid).
            m_vol: vol-bin multiplier from VolBinClassifier.
            m_dd: drawdown multiplier from dd_multiplier.
            h_tox: toxicity component in price units (Phase 1: 0).
            h_event: event-risk component in price units (Phase 1: 0).

        Returns:
            Half-spread in price units. Caller places bid at FV - h, ask at FV + h.
        """
        if mid <= 0:
            return 0.0

        bps_to_price = mid / 10_000

        h_fee = self._p.h_fee_bps * bps_to_price
        rebate = self._p.rebate_credit_bps * bps_to_price

        # HIP-3 growth mode: scale down fee and rebate components
        if self._p.growth_mode:
            h_fee *= self._p.growth_mode_scale
            rebate *= self._p.growth_mode_scale

        h_vol = sigma_price * self._p.vol_scale

        # Core half-spread: max of tick floor vs sum of components
        h_raw = max(
            0.5 * self._tick_size,
            h_fee + h_vol + h_tox + h_event,
        ) - rebate

        # Ensure non-negative after rebate
        h_raw = max(h_raw, 0.5 * self._tick_size)

        # Apply risk multipliers (sqrt on vol, full on DD)
        h_amplified = h_raw * math.sqrt(m_vol) * m_dd

        # Clamp to configured bounds (bounds are full-spread bps, so halve them)
        h_min = (self._p.min_spread_bps / 2.0) * bps_to_price
        h_max = (self._p.max_spread_bps / 2.0) * bps_to_price

        return max(h_min, min(h_amplified, h_max))
