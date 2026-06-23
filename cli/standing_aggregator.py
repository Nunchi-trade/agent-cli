"""StandingAggregator — derive HOUSE Standing rows from the local trade log.

Python port of agent-command-center/server/src/standing-aggregator.ts.

The HL public /info API does not expose builder-attributed flow per market for an
arbitrary builder address (real cross-institution attribution needs a HyperEVM
event indexer). For the demo + first-run pilot, the honest substitute is the
trades the local fleet actually placed: the engine writes one JSONL line per fill
to data/cli/trades.jsonl with schema:

    {tick, oid, instrument, side, price, quantity, timestamp_ms, fee}

This groups those lines by `instrument`, computes 24h / 7d notional, fill counts,
and BC accrued (notional × fee fraction). A 5-second TTL cache keeps repeated
polling from re-reading the file each time.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from cli.builder_fee import BuilderFeeConfig
from common.fleet_models import FleetStandingMarket, FleetStandingResult

DAY_MS = 24 * 60 * 60 * 1000
WEEK_MS = 7 * DAY_MS
CACHE_TTL_MS = 5_000


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_trades_path() -> Path:
    return _repo_root() / "data" / "cli" / "trades.jsonl"


class StandingAggregator:
    """Aggregates trades.jsonl into a FleetStandingResult, with a 5s TTL cache."""

    def __init__(
        self,
        trades_path: Optional[Path] = None,
        fee_config: Optional[BuilderFeeConfig] = None,
    ) -> None:
        self.trades_path = Path(trades_path) if trades_path else _default_trades_path()
        # BuilderFeeConfig.from_env() honours BUILDER_FEE_TENTHS_BPS, else default.
        self.fee_config = fee_config or BuilderFeeConfig.from_env()
        self._cache: Optional[FleetStandingResult] = None
        self._cache_at_ms: int = 0

    def get_standing(self, bypass_cache: bool = False) -> FleetStandingResult:
        now = _now_ms()
        if (
            not bypass_cache
            and self._cache is not None
            and now - self._cache_at_ms < CACHE_TTL_MS
        ):
            return self._cache

        result = self._compute(now)
        self._cache = result
        self._cache_at_ms = now
        return result

    # ----------------------------------------------------------- internals

    def _compute(self, now: int) -> FleetStandingResult:
        fee_tenths_bps = self.fee_config.fee_rate_tenths_bps
        # tenths-of-bps → fraction of notional.
        #   100 tenths-bps = 10 bps = 0.1% = 1e-3 = 100 / 100_000.  ✓
        fee_fraction = fee_tenths_bps / 100_000

        if not self.trades_path.exists():
            return FleetStandingResult(
                fee_rate_tenths_bps=fee_tenths_bps,
                as_of_ms=now,
                empty=True,
                error=f"trades log not found at {self.trades_path}",
            )

        try:
            raw = self.trades_path.read_text()
        except OSError as err:
            return FleetStandingResult(
                fee_rate_tenths_bps=fee_tenths_bps,
                as_of_ms=now,
                empty=True,
                error=f"read failed: {err}",
            )

        cutoff_24h = now - DAY_MS
        cutoff_7d = now - WEEK_MS

        # bucket: instrument -> [n24, n7, f24, f7]
        buckets: Dict[str, List[float]] = {}
        total_fills = 0

        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue

            instrument = row.get("instrument")
            ts = row.get("timestamp_ms")
            if not instrument or ts is None:
                continue
            try:
                ts = int(ts)
                price = float(row.get("price"))
                qty = float(row.get("quantity"))
            except (TypeError, ValueError):
                continue
            notional = price * qty
            if notional <= 0:
                continue

            total_fills += 1
            b = buckets.setdefault(instrument, [0.0, 0.0, 0.0, 0.0])
            if ts >= cutoff_7d:
                b[1] += notional  # n7
                b[3] += 1  # f7
                if ts >= cutoff_24h:
                    b[0] += notional  # n24
                    b[2] += 1  # f24

        markets: List[FleetStandingMarket] = []
        total_n24 = total_n7 = total_bc24 = total_bc7 = 0.0
        for instrument, (n24, n7, f24, f7) in buckets.items():
            bc24 = n24 * fee_fraction
            bc7 = n7 * fee_fraction
            markets.append(
                FleetStandingMarket(
                    market=instrument,
                    notional_24h=n24,
                    notional_7d=n7,
                    fill_count_24h=int(f24),
                    fill_count_7d=int(f7),
                    bc_accrued_24h=bc24,
                    bc_accrued_7d=bc7,
                )
            )
            total_n24 += n24
            total_n7 += n7
            total_bc24 += bc24
            total_bc7 += bc7

        # Sort by 24h notional desc (matches the TS dashboard ordering).
        markets.sort(key=lambda m: m.notional_24h, reverse=True)

        return FleetStandingResult(
            total_fills=total_fills,
            total_notional_24h=total_n24,
            total_notional_7d=total_n7,
            total_bc_accrued_24h=total_bc24,
            total_bc_accrued_7d=total_bc7,
            markets=markets,
            fee_rate_tenths_bps=fee_tenths_bps,
            as_of_ms=now,
            empty=len(markets) == 0,
        )


def _now_ms() -> int:
    return int(time.time() * 1000)
