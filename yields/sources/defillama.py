"""DefiLlamaSource — broad, read-only yield discovery via the DeFiLlama API.

DeFiLlama's ``yields.llama.fi/pools`` endpoint aggregates APY/TVL across
thousands of pools on every chain. This source pulls it, filters to the
requested chains, and maps each row to a :class:`YieldOpportunity`. It is the
Tier 1 discovery surface — broad coverage, but read-only: every opportunity it
emits has ``has_onchain_adapter=False`` and ``pool_address=None`` (DeFiLlama's
``pool`` field is its own UUID, not an EVM contract address — it is stashed in
``raw`` instead).

Verified response shape (``yields.llama.fi/pools``, 2026-05-18, against the
DeFiLlama yield-server schema)::

    {"status": "success", "data": [ {pool row}, ... ]}

A pool row carries: ``pool`` (string UUID), ``chain`` (capitalized name, e.g.
"Ethereum" / "Base"), ``project`` (protocol slug), ``symbol``, ``tvlUsd``,
``apyBase`` (nullable), ``apyReward`` (nullable), ``apy`` (nullable total),
``underlyingTokens`` (nullable address list), ``rewardTokens``, ``poolMeta``.

The source is defensive end-to-end (per the ``YieldSource`` contract):
``discover()`` catches every network/parse error and returns a possibly empty
list — it never raises into the aggregator. A best-effort on-disk cache lets a
scan still return data briefly when the API is unreachable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, ClassVar, Optional, Sequence

import requests

from yields.models import (
    Chain,
    SourceTier,
    TokenRef,
    YieldKind,
    YieldOpportunity,
    canonical_id,
)
from yields.sources.base import YieldSource

log = logging.getLogger(__name__)

#: Default DeFiLlama yields endpoint. Overridable via ``NUNCHI_DEFILLAMA_URL``.
_DEFAULT_URL = "https://yields.llama.fi/pools"
_HTTP_TIMEOUT_S = 20
#: Cache freshness window — a cached snapshot older than this is ignored.
_CACHE_TTL_S = 6 * 3600
_CACHE_PATH = Path.home() / ".nunchi" / "yields-cache" / "defillama.json"

# DeFiLlama capitalizes chain names; map our Chain enum onto those labels.
_CHAIN_LABEL: dict[Chain, str] = {
    Chain.ethereum: "Ethereum",
    Chain.base: "Base",
}

# Heuristic project-slug -> YieldKind. DeFiLlama does not return a clean kind;
# unknown projects fall through to ``YieldKind.lending`` which is the dominant
# category on money-market-heavy chains and never wrong enough to mislead the
# optimizer (kind is informational, not load-bearing for routing).
_STAKING_HINTS = ("lido", "rocket-pool", "stakewise", "frax-ether", "stader")
_VAULT_HINTS = ("sky-", "makerdao", "spark", "yearn", "morpho", "erc4626")
_LP_HINTS = ("uniswap", "curve", "balancer", "aerodrome", "velodrome", "pancakeswap")


def _classify_kind(project: str, symbol: str) -> YieldKind:
    """Best-effort YieldKind from a DeFiLlama project slug / symbol."""
    p = project.lower()
    if any(h in p for h in _STAKING_HINTS):
        return YieldKind.staking
    if any(h in p for h in _VAULT_HINTS):
        return YieldKind.vault
    if any(h in p for h in _LP_HINTS):
        return YieldKind.lp
    # An LP symbol usually contains a separator ("USDC-WETH"); a single-asset
    # money-market row does not.
    if "-" in symbol and project:
        return YieldKind.lp
    return YieldKind.lending


def _normalize_protocol(project: str) -> str:
    """Normalize a DeFiLlama project name to a stable lowercase slug.

    DeFiLlama already returns hyphenated slugs ("aave-v3", "lido"); this just
    lowercases and trims so it dedups cleanly against on-chain adapter slugs.
    """
    return (project or "").strip().lower()


class DefiLlamaSource(YieldSource):
    """Tier 1 read-only discovery backed by the DeFiLlama yields API."""

    tier: ClassVar[SourceTier] = SourceTier.defillama
    name: ClassVar[str] = "defillama"

    def __init__(self, *, url: Optional[str] = None, use_cache: bool = True) -> None:
        # Explicit arg wins; else the env override; else the default endpoint.
        self.url = url or os.environ.get("NUNCHI_DEFILLAMA_URL", "").strip() or _DEFAULT_URL
        self.use_cache = use_cache

    # --- discovery --------------------------------------------------------
    def discover(self, chains: Sequence[Chain]) -> list[YieldOpportunity]:
        """Fetch DeFiLlama pools and map them onto the requested chains.

        Never raises: a network failure, non-200, or malformed body all
        degrade to a (cached if available, else empty) result.
        """
        wanted = {c for c in chains}
        if not wanted:
            return []
        labels = {_CHAIN_LABEL[c] for c in wanted if c in _CHAIN_LABEL}

        rows = self._fetch_rows()
        if rows is None:
            cached = self._read_cache()
            if cached is None:
                log.warning("defillama: no live data and no usable cache — returning []")
                return []
            log.warning("defillama: live fetch failed — serving %d cached rows", len(cached))
            rows = cached
        else:
            self._write_cache(rows)

        fetched_ms = int(time.time() * 1000)
        out: list[YieldOpportunity] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("chain", "")) not in labels:
                continue
            opp = self._parse_pool_row(row, fetched_ms)
            if opp is not None:
                out.append(opp)
        log.info("defillama: %d opportunities across %s", len(out), sorted(labels))
        return out

    # --- HTTP -------------------------------------------------------------
    def _fetch_rows(self) -> Optional[list[Any]]:
        """GET the pools endpoint; return the ``data`` list, or None on any
        failure. All shape/transport assumptions are contained here."""
        try:
            resp = requests.get(
                self.url,
                timeout=_HTTP_TIMEOUT_S,
                headers={"Accept": "application/json"},
            )
        except requests.RequestException as exc:
            log.warning("defillama: request to %s failed: %s", self.url, exc)
            return None
        if resp.status_code != 200:
            log.warning("defillama: %s returned HTTP %s", self.url, resp.status_code)
            return None
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            log.warning("defillama: response body was not valid JSON: %s", exc)
            return None
        # Documented shape is {"status": "success", "data": [...]}; tolerate a
        # bare list too in case the endpoint shape ever changes.
        if isinstance(body, dict):
            data = body.get("data")
        elif isinstance(body, list):
            data = body
        else:
            data = None
        if not isinstance(data, list):
            log.warning("defillama: response had no 'data' list — got %s", type(body))
            return None
        return data

    # --- row mapping ------------------------------------------------------
    def _parse_pool_row(
        self, row: dict[str, Any], fetched_ms: int
    ) -> Optional[YieldOpportunity]:
        """Map one DeFiLlama pool row to a YieldOpportunity.

        Every field access is a defensive ``.get()``; a row that cannot be
        mapped is skipped (logged at debug) rather than aborting the scan.
        """
        try:
            chain_label = str(row.get("chain", ""))
            chain = _label_to_chain(chain_label)
            if chain is None:
                return None

            project = str(row.get("project", "")).strip()
            protocol = _normalize_protocol(project)
            if not protocol:
                return None
            symbol = str(row.get("symbol", "")).strip()

            apy_base = _as_apy(row.get("apyBase"))
            apy_reward = _as_apy(row.get("apyReward"))
            # When DeFiLlama gives only a total ``apy`` (no breakdown), treat it
            # as base — the optimizer reads apy_total either way.
            if apy_base == 0.0 and apy_reward == 0.0:
                apy_base = _as_apy(row.get("apy"))

            tvl_usd = _as_float(row.get("tvlUsd"))
            kind = _classify_kind(project, symbol)

            underlying = _build_underlying(row.get("underlyingTokens"), symbol, chain)
            under_addrs = [t.address for t in underlying if t.address]

            # DeFiLlama's ``pool`` field is its own UUID, never an EVM address.
            llama_pool_id = row.get("pool")
            opp_id = canonical_id(
                chain=chain.value,
                protocol=protocol,
                underlying_addresses=under_addrs,
                pool_address=None,
                kind=kind.value,
            )
            return YieldOpportunity(
                id=opp_id,
                protocol=protocol,
                chain=chain,
                kind=kind,
                pool_address=None,
                underlying=underlying,
                receipt_token=None,
                apy_base=apy_base,
                apy_reward=apy_reward,
                tvl_usd=tvl_usd,
                source_tier=SourceTier.defillama,
                has_onchain_adapter=False,
                fetched_at_ms=fetched_ms,
                raw={
                    "defillama_pool_id": llama_pool_id,
                    "project": project,
                    "symbol": symbol,
                    "poolMeta": row.get("poolMeta"),
                    "apy": row.get("apy"),
                    "apyBase": row.get("apyBase"),
                    "apyReward": row.get("apyReward"),
                    "stablecoin": row.get("stablecoin"),
                    "ilRisk": row.get("ilRisk"),
                    "exposure": row.get("exposure"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the scan
            log.debug("defillama: skipping unparseable row (%s): %r", exc, row.get("pool"))
            return None

    # --- on-disk cache (best effort) -------------------------------------
    def _write_cache(self, rows: list[Any]) -> None:
        if not self.use_cache:
            return
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {"cached_at_ms": int(time.time() * 1000), "data": rows}
            _CACHE_PATH.write_text(json.dumps(payload))
        except OSError as exc:  # caching is a nicety — never fatal
            log.debug("defillama: could not write cache: %s", exc)

    def _read_cache(self) -> Optional[list[Any]]:
        if not self.use_cache or not _CACHE_PATH.is_file():
            return None
        try:
            payload = json.loads(_CACHE_PATH.read_text())
        except (OSError, ValueError) as exc:
            log.debug("defillama: could not read cache: %s", exc)
            return None
        cached_at = payload.get("cached_at_ms", 0) if isinstance(payload, dict) else 0
        age_s = (int(time.time() * 1000) - int(cached_at)) / 1000.0
        if age_s > _CACHE_TTL_S:
            log.debug("defillama: cache is stale (%.0fs old) — ignoring", age_s)
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else None


# --- module-level parse helpers (no shape assumption escapes this file) -----
def _label_to_chain(label: str) -> Optional[Chain]:
    """Map a DeFiLlama chain label back to our Chain enum (None if unsupported)."""
    for chain, lbl in _CHAIN_LABEL.items():
        if lbl == label:
            return chain
    return None


def _as_float(value: Any) -> float:
    """Coerce a possibly-null/str numeric to float; default 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_apy(value: Any) -> float:
    """DeFiLlama reports APY in percent (4.3 == 4.3%); our models use the
    fraction (0.043). Convert here. Null/garbage -> 0.0."""
    return _as_float(value) / 100.0


def _build_underlying(
    tokens: Any, symbol: str, chain: Chain
) -> list[TokenRef]:
    """Build TokenRefs from DeFiLlama's ``underlyingTokens`` address list.

    DeFiLlama gives addresses without symbols/decimals; we attach the addresses
    and leave symbol best-effort from the pool symbol. ``decimals`` stays None
    (the optimizer never needs it for a DeFiLlama-only row)."""
    if not isinstance(tokens, list) or not tokens:
        # No address list — keep a symbol-only ref so the row is still usable.
        sym = (symbol or "").strip()
        return [TokenRef(symbol=sym, chain=chain)] if sym else []
    refs: list[TokenRef] = []
    sym_parts = [s for s in (symbol or "").replace("/", "-").split("-") if s]
    for i, addr in enumerate(tokens):
        if not isinstance(addr, str) or not addr.startswith("0x"):
            continue
        sym = sym_parts[i] if i < len(sym_parts) else ""
        refs.append(TokenRef(symbol=sym, chain=chain, address=addr))
    return refs
