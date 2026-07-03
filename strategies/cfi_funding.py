"""CFI v2 funding inputs from Hyperliquid only.

No SEDA/oracle dependency — hedge math stays in `strategies/cfi_hedge.py`.
K2 fixed leg and r_ema are replayed from HL `fundingHistory`; current funding
from `metaAndAssetCtxs`.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal, Optional

import requests

from strategies.cfi_hedge import (
    BTCSWP_PROFILE,
    CFIAssetProfile,
    FundingRateSample,
    compute_k2_from_history,
)

log = logging.getLogger(__name__)

HL_INFO_URL_DEFAULT = "https://api.hyperliquid.xyz/info"


def _hl_info_url() -> str:
    return os.environ.get("HL_INFO_URL", HL_INFO_URL_DEFAULT)


@dataclass(frozen=True)
class CfiFundingSnapshot:
    """K2 + r_ema computed from HL funding history."""

    source: Literal["hl"]
    timestamp_iso: str
    timestamp_ms: int
    oracle_px: Optional[float]
    k_fixed_hr: float
    r_ema_hr: float
    cfi: Optional[float]


def _fetch_funding_history(
    coin: str,
    lookback_hours: int,
    *,
    timeout_s: float = 10.0,
) -> list[dict]:
    """Pull HL `fundingHistory` for a coin over the lookback window."""
    end_time = int(time.time() * 1000)
    start_time = end_time - lookback_hours * 60 * 60 * 1000
    resp = requests.post(
        _hl_info_url(),
        json={
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time,
            "endTime": end_time,
        },
        timeout=timeout_s,
    )
    resp.raise_for_status()
    return resp.json() or []


def fetch_hl_current_funding_hr(
    coin: str,
    *,
    timeout_s: float = 10.0,
) -> Optional[float]:
    """Predicted current hourly funding rate for an HL coin."""
    try:
        resp = requests.post(
            _hl_info_url(),
            json={"type": "metaAndAssetCtxs"},
            timeout=timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        universe = data[0]["universe"]
        ctxs = data[1]
        for meta, ctx in zip(universe, ctxs):
            if meta.get("name") == coin:
                return float(ctx.get("funding", 0))
        return None
    except Exception as e:
        log.warning("cfi_funding: fetch_hl_current_funding_hr(%s) failed: %s", coin, e)
        return None


def replay_k2_from_hl(
    profile: CFIAssetProfile = BTCSWP_PROFILE,
    *,
    lookback_hours: int = 168,
    timeout_s: float = 10.0,
) -> CfiFundingSnapshot:
    """Compute K2 + r_ema locally from HL `fundingHistory`."""
    hist = _fetch_funding_history(
        profile.hl_coin,
        lookback_hours,
        timeout_s=timeout_s,
    )
    samples = [
        FundingRateSample(
            funding_rate=float(e["fundingRate"]),
            time=int(e["time"]),
        )
        for e in hist
    ]
    k = compute_k2_from_history(samples, profile)

    if samples:
        r_ema = samples[0].funding_rate
        for s in samples:
            r_ema = (1.0 - profile.k2_beta) * r_ema + profile.k2_beta * s.funding_rate
    else:
        r_ema = profile.fixed_leg_initial

    now_ms = int(time.time() * 1000)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000))
    return CfiFundingSnapshot(
        source="hl",
        timestamp_iso=now_iso,
        timestamp_ms=now_ms,
        oracle_px=None,
        k_fixed_hr=k,
        r_ema_hr=r_ema,
        cfi=None,
    )


def fetch_cfi_funding_snapshot(
    profile: CFIAssetProfile = BTCSWP_PROFILE,
    *,
    timeout_s: float = 10.0,
) -> CfiFundingSnapshot:
    """Live K2 inputs from HL funding history only."""
    return replay_k2_from_hl(profile, timeout_s=timeout_s)


# Back-compat aliases for callers migrating off seda_oracle naming.
BTCSWPOracleSnapshot = CfiFundingSnapshot
fetch_btcswp_snapshot = fetch_cfi_funding_snapshot
