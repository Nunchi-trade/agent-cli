"""yields.aggregator — pull every source, normalize, dedup, risk-score.

Two-tier discovery: the broad DeFiLlama source plus the curated on-chain
adapters. The aggregator merges rows that describe the same pool:

* on-chain data wins the execution-critical fields (it is authoritative and
  reads the current block) — `pool_address`, `receipt_token`, `apy_base`,
  `has_onchain_adapter`;
* DeFiLlama wins `tvl_usd` (it aggregates global TVL better) and `apy_reward`
  (incentive APYs are hard to read on-chain).

Dedup is on a STRICT composite key — never a fuzzy name match, never across
chains. A row that cannot be confidently keyed is kept un-merged: a duplicate
display row is a far smaller harm than a wrong merge that mis-routes a deposit.

This module DOES do I/O (it calls the sources). The pure decision modules are
`yields.risk` and `yields.optimizer`.

NOTE (agent-cli port): the on-chain (Tier 2) adapters need the EVM execution
substrate (`common.evm.*`, `trading.dex.*`) which is not yet present in
agent-cli, so the import below is optional. When the substrate lands, the
``yields.sources.onchain`` package becomes importable and the on-chain adapters
light up automatically with no further change here. Until then the aggregator
runs the DeFiLlama (Tier 1) read-only source only.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from yields.models import Chain, SourceTier, YieldOpportunity
from yields.risk import DEFAULT_RISK_CONFIG, RiskConfig, score_opportunity
from yields.sources.base import YieldSource
from yields.sources.defillama import DefiLlamaSource

try:  # on-chain adapters require the EVM execution substrate (see module docstring)
    from yields.sources.onchain import ONCHAIN_ADAPTERS
except ImportError:  # substrate absent in this build — Tier 1 (DeFiLlama) only
    ONCHAIN_ADAPTERS: list[type[YieldSource]] = []

_log = logging.getLogger(__name__)

# DeFiLlama project slugs that denote a protocol modeled here under a different
# canonical slug. Best-effort — an un-normalized slug merely yields a duplicate
# display row, never a wrong merge.
_PROTOCOL_ALIASES: dict[str, str] = {
    "aave": "aave-v3",
    "aave-v2": "aave-v3",
    "compound": "compound-v3",
    "makerdao": "sky",
    "spark": "sky",
    "sky-lending": "sky",
    "lido-steth": "lido",
}

_DEFAULT_TVL_FLOOR_USD = 100_000.0


def normalize_protocol(slug: str) -> str:
    """Collapse a source's protocol slug onto this repo's canonical slug."""
    s = (slug or "").strip().lower()
    return _PROTOCOL_ALIASES.get(s, s)


def default_sources() -> list[YieldSource]:
    """The standard source set — DeFiLlama plus every on-chain adapter."""
    sources: list[YieldSource] = [DefiLlamaSource()]
    sources.extend(adapter_cls() for adapter_cls in ONCHAIN_ADAPTERS)
    return sources


def collect(
    chains: Sequence[Chain],
    *,
    sources: Optional[Sequence[YieldSource]] = None,
) -> list[YieldOpportunity]:
    """Run every source's `discover()` and return the flat, un-merged list.

    Each source is already defensive (returns `[]` on its own failure); the
    extra try/except here is belt-and-suspenders so one bad source can never
    break a scan.
    """
    srcs = list(sources) if sources is not None else default_sources()
    out: list[YieldOpportunity] = []
    for src in srcs:
        try:
            out.extend(src.discover(chains))
        except Exception as exc:  # noqa: BLE001 - a source must never break a scan
            _log.warning("yield source %s failed: %s", getattr(src, "name", src), exc)
    return out


def _merge_key(opp: YieldOpportunity) -> tuple:
    """Strict composite identity for dedup: chain + canonical protocol +
    underlying symbol set + kind. Never crosses chains; never a fuzzy match."""
    symbols = frozenset(
        (t.symbol or "").strip().upper() for t in opp.underlying if t.symbol
    )
    return (opp.chain.value, normalize_protocol(opp.protocol), symbols, opp.kind.value)


def _merge_pair(a: YieldOpportunity, b: YieldOpportunity) -> YieldOpportunity:
    """Merge two opportunities for the same pool.

    The on-chain row (if either is on-chain) is authoritative for execution;
    the other contributes TVL and reward APY.
    """
    onchain, other = a, b
    if a.source_tier != SourceTier.onchain and b.source_tier == SourceTier.onchain:
        onchain, other = b, a

    merged = onchain.model_copy(deep=True)
    if other.tvl_usd > 0:                       # DeFiLlama aggregates TVL better
        merged.tvl_usd = other.tvl_usd
    if onchain.source_tier == SourceTier.onchain and onchain.apy_reward == 0.0:
        merged.apy_reward = other.apy_reward     # on-chain rarely reads incentives
    merged.raw = {**other.raw, **onchain.raw, "merged_from": [a.id, b.id]}
    return merged


def aggregate(
    chains: Sequence[Chain],
    *,
    sources: Optional[Sequence[YieldSource]] = None,
    risk_cfg: RiskConfig = DEFAULT_RISK_CONFIG,
    tvl_floor_usd: float = _DEFAULT_TVL_FLOOR_USD,
) -> list[YieldOpportunity]:
    """Collect, drop dust, dedup/merge, risk-score; return sorted by gross APY."""
    raw = collect(chains, sources=sources)
    merged: dict[tuple, YieldOpportunity] = {}
    unkeyable: list[YieldOpportunity] = []

    for opp in raw:
        # drop dust pools — a tiny TVL with a huge APY distorts ranking.
        # tvl_usd == 0 means "unknown" (on-chain adapters leave it 0), not "tiny".
        if opp.tvl_usd and opp.tvl_usd < tvl_floor_usd:
            continue
        key = _merge_key(opp)
        if not key[2]:  # no underlying symbols — cannot be safely keyed
            unkeyable.append(opp)
            continue
        if key in merged:
            merged[key] = _merge_pair(merged[key], opp)
            _log.debug("merged duplicate yield row: %s", key)
        else:
            merged[key] = opp

    result = list(merged.values()) + unkeyable
    for opp in result:
        opp.risk_score = score_opportunity(opp, risk_cfg)
    result.sort(key=lambda o: o.apy_total, reverse=True)
    return result
