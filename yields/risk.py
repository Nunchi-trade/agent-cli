"""yields.risk — transparent, pure risk scoring for yield opportunities.

A score in [0, 1], lower = safer. It is a weighted sum of five sub-scores,
every weight and threshold living in ``RiskConfig`` so the model is inspectable
and tunable::

    risk = w_tvl*r_tvl + w_protocol*r_protocol + w_reward*r_reward
         + w_peg*r_peg + w_chain*r_chain                  (weights sum to 1.0)

Sub-scores (each in [0, 1]):
  r_tvl       depth — clamp(1 - log10(tvl/floor)/decades): a $1M pool -> 1.0,
              a $1B pool -> 0.0. A shallow pool is easier to drain / distort.
  r_protocol  smart-contract maturity, from the curated PROTOCOL_TIERS table.
  r_reward    incentive fragility — reward_apy / total_apy (token emissions end).
  r_peg       stablecoin de-peg risk of the underlying, from STABLE_PEG_RISK.
  r_chain     chain risk — 0 for Ethereum L1, a small prior for an L2.

Anti-gameability: every input is either measured on-chain (TVL) or read from a
table this repo controls — never self-reported by the pool. Only a maintainer
editing a table can change a protocol's safety rating.

This module is PURE — no network, no chain, no clock, no env. It imports only
the models and the stdlib, so it ports cleanly to frontend-integration.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from yields.models import YieldOpportunity

# Smart-contract maturity tiers — lower = safer. Keyed by normalized protocol
# slug. Unlisted protocols fall back to UNKNOWN_PROTOCOL_RISK (conservative).
PROTOCOL_TIERS: dict[str, float] = {
    # bluechip — multi-year track record, heavily audited, deep TVL
    "aave-v3": 0.08,
    "lido": 0.10,
    "sky": 0.12,
    "compound-v3": 0.12,
    "morpho-blue": 0.18,
    # established — audited and in production, but younger or more complex
    "moonwell": 0.32,
    "curve": 0.28,
    "convex": 0.32,
    "aerodrome": 0.34,
    "pendle": 0.40,
    "ethena": 0.42,
}
UNKNOWN_PROTOCOL_RISK = 0.70

# De-peg risk of stable underlyings. Non-stable assets (ETH, WETH, ...) carry
# 0.0 here — their price volatility is the depositor's chosen exposure, not a
# risk of the opportunity itself.
STABLE_PEG_RISK: dict[str, float] = {
    "usdc": 0.06, "usdt": 0.08, "dai": 0.08, "usdbc": 0.08,
    "usds": 0.10, "pyusd": 0.10, "crvusd": 0.18, "gho": 0.18,
    "frax": 0.20, "susde": 0.22, "usde": 0.22,
}


@dataclass(frozen=True)
class RiskConfig:
    """Weights and thresholds for the risk score. The five weights sum to 1.0."""

    w_tvl: float = 0.30
    w_protocol: float = 0.35
    w_reward: float = 0.15
    w_peg: float = 0.12
    w_chain: float = 0.08
    tvl_floor_usd: float = 1_000_000.0   # TVL at/below this scores r_tvl = 1.0
    tvl_decades: float = 3.0             # decades of TVL above the floor -> r_tvl = 0.0
    chain_risk: dict = field(default_factory=lambda: {"ethereum": 0.0, "base": 0.12})

    def validate(self) -> None:
        total = self.w_tvl + self.w_protocol + self.w_reward + self.w_peg + self.w_chain
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"RiskConfig weights must sum to 1.0, got {total}")


DEFAULT_RISK_CONFIG = RiskConfig()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def r_tvl(tvl_usd: float, cfg: RiskConfig) -> float:
    """Depth sub-score. A shallow pool -> 1.0 (risky); a deep pool -> 0.0."""
    if tvl_usd <= 0:
        return 1.0
    decades_above_floor = math.log10(max(tvl_usd, 1.0) / cfg.tvl_floor_usd)
    return _clamp01(1.0 - decades_above_floor / cfg.tvl_decades)


def r_protocol(protocol: str) -> float:
    """Smart-contract maturity sub-score from the curated tier table."""
    return PROTOCOL_TIERS.get((protocol or "").strip().lower(), UNKNOWN_PROTOCOL_RISK)


def r_reward(opp: YieldOpportunity) -> float:
    """Incentive fragility — the share of APY that is token emissions."""
    total = opp.apy_base + opp.apy_reward
    if total <= 0:
        return 0.0
    return _clamp01(opp.apy_reward / total)


def r_peg(opp: YieldOpportunity) -> float:
    """Max de-peg risk across the opportunity's stable underlyings."""
    risks = [STABLE_PEG_RISK.get((t.symbol or "").lower(), 0.0) for t in opp.underlying]
    return max(risks) if risks else 0.0


def r_chain(opp: YieldOpportunity, cfg: RiskConfig) -> float:
    """Chain-level risk prior (L2 sequencer / bridge surface)."""
    return cfg.chain_risk.get(opp.chain.value, 0.10)


def breakdown(opp: YieldOpportunity, cfg: RiskConfig = DEFAULT_RISK_CONFIG) -> dict[str, float]:
    """The five WEIGHTED components — they sum to the score. For transparency
    (e.g. a `nunchi yield` "why is this risky" view)."""
    return {
        "tvl": cfg.w_tvl * r_tvl(opp.tvl_usd, cfg),
        "protocol": cfg.w_protocol * r_protocol(opp.protocol),
        "reward": cfg.w_reward * r_reward(opp),
        "peg": cfg.w_peg * r_peg(opp),
        "chain": cfg.w_chain * r_chain(opp, cfg),
    }


def score_opportunity(
    opp: YieldOpportunity, cfg: RiskConfig = DEFAULT_RISK_CONFIG
) -> float:
    """Risk score in [0, 1] — lower is safer. Pure: deterministic, no I/O."""
    return _clamp01(sum(breakdown(opp, cfg).values()))
