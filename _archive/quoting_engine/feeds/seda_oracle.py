"""BTCSWP SEDA oracle client.

Live K2 fixed-leg fetcher. Mirrors `~/demo-ide/src/lib/btcswp-oracle.ts` and
`~/UI-BTCSWP-Fixed/src/api.ts` — the production source-of-truth for the
deployed BTCSWP-on-YEX market.

Falls back to local K2 EMA replay over HL `fundingHistory` when SEDA is
unreachable (offline demo, API key issue, asset without a deployed oracle)
so the hedge tile always renders a usable proposal.
"""
from __future__ import annotations

import json
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


# ─── Endpoint config ────────────────────────────────────────────────────────
# Defaults match the deployed BTCSWP-on-YEX market. Override via env to
# point at a different oracle or a local replay node.

DEFAULT_SEDA_BASE_URL = "https://fast-api.testnet.seda.xyz"
DEFAULT_SEDA_API_KEY = (
    # Testnet public-ish demo key — same one shipped in UI-BTCSWP-Fixed
    # README so the demo works out-of-the-box. Production keys should be
    # injected via env in deployed builds.
    "fast_test_Wa1H5Ch7_5vg6zyjYzMgcPGRkNTw636h734WGYi"
)
DEFAULT_SEDA_PROGRAM_ID = (
    "562235979e26f7381a362f7d6f76f6f2f74c820db1537f9b3e8b91a106e3c98a"
)
HL_INFO_URL_DEFAULT = "https://api.hyperliquid.xyz/info"


def _seda_base_url() -> str:
    return os.environ.get("SEDA_BASE_URL", DEFAULT_SEDA_BASE_URL)


def _seda_api_key() -> str:
    return (
        os.environ.get("SEDA_API_KEY")
        or os.environ.get("SEDA_FAST_TESTNET_API_KEY")
        or DEFAULT_SEDA_API_KEY
    )


def _seda_program_id() -> str:
    return os.environ.get("SEDA_PROGRAM_ID", DEFAULT_SEDA_PROGRAM_ID)


def _hl_info_url() -> str:
    return os.environ.get("HL_INFO_URL", HL_INFO_URL_DEFAULT)


# ─── Public types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BTCSWPOracleSnapshot:
    """Live K2 + r_ema + (optional) wire price + CFI from the SEDA oracle.

    `source` == "seda" when the deployed oracle answered, "replay" when we
    computed K2 locally from HL `fundingHistory` because SEDA was
    unreachable.
    """

    source: Literal["seda", "replay"]
    timestamp_iso: str
    timestamp_ms: int
    oracle_px: Optional[float]
    k_fixed_hr: float
    r_ema_hr: float
    cfi: Optional[float]


# ─── Fetch ──────────────────────────────────────────────────────────────────


def _exec_inputs_for(profile: CFIAssetProfile) -> dict:
    """Build the SEDA `execInputs` payload for a deployed CFI v2 market."""
    return {
        "hl_ref_asset_name": profile.hl_coin,
        # The deployed BTCSWP market reads the Stork "current funding" feed.
        # Sibling markets (ETHSWP) follow the same naming once deployed.
        "stork_ref_asset_name": f"HL_{profile.hl_coin}_CURRENT_FUNDING",
        "hl_config": {
            "dex": "yex",
            "assetName": profile.cfi_asset_name,
            "assetIndex": profile.cfi_asset_index,
        },
        "baseline_b0": str(profile.baseline_b0),
        "fixed_leg_initial": str(profile.fixed_leg_initial),
        "k2_beta": str(profile.k2_beta),
        "vol_mult_l": str(profile.vol_mult_l),
    }


def fetch_seda_snapshot(
    profile: CFIAssetProfile = BTCSWP_PROFILE,
    *,
    timeout_s: float = 10.0,
) -> BTCSWPOracleSnapshot:
    """Hit the SEDA oracle. Throws on any failure — caller falls back."""
    params = {
        "execProgramId": _seda_program_id(),
        "execInputs": json.dumps(_exec_inputs_for(profile)),
        "encoding": "utf8",
        "returnLastResult": "true",
    }
    headers = {
        "Authorization": f"Bearer {_seda_api_key()}",
        "Accept": "application/json",
    }
    resp = requests.get(
        f"{_seda_base_url()}/execute",
        params=params,
        headers=headers,
        timeout=timeout_s,
    )
    if not resp.ok:
        raise RuntimeError(
            f"SEDA {resp.status_code}: {resp.text[:200]}"
        )
    envelope = resp.json()
    # Response is nested: { _tag: "ExecuteResponse", data: { ..., result: "<JSON string>" } }
    result_str = (
        envelope.get("data", {}).get("result")
        or envelope.get("result")
    )
    if not isinstance(result_str, str):
        raise RuntimeError("SEDA: missing result string")
    parsed = json.loads(result_str)
    state = parsed["state"]
    return BTCSWPOracleSnapshot(
        source="seda",
        timestamp_iso=parsed["timestamp"],
        timestamp_ms=parsed["timestamp_millis"],
        oracle_px=float(parsed.get("oracle_px")) if parsed.get("oracle_px") is not None else None,
        k_fixed_hr=float(state["k_fixed_hr"]),
        r_ema_hr=float(state["r_ema"]),
        cfi=float(state["cfi"]) if state.get("cfi") is not None else None,
    )


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
    """Predicted current hourly funding rate for an HL coin.

    Mirrors `fetch_current_funding_rate` from
    `~/hyperliquid-funding-rate-perps/tools/hedge_calculator.py`. HL
    transitioned to hourly funding in 2024; the `funding` field on
    `metaAndAssetCtxs` is the predicted hourly fraction.

    Returns None if the coin isn't in the universe or the call fails.
    """
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
        log.warning("seda_oracle: fetch_hl_current_funding_hr(%s) failed: %s", coin, e)
        return None


def replay_k2_from_hl(
    profile: CFIAssetProfile = BTCSWP_PROFILE,
    *,
    lookback_hours: int = 168,
    timeout_s: float = 10.0,
) -> BTCSWPOracleSnapshot:
    """Compute K2 + r_ema locally from HL `fundingHistory`.

    Used when SEDA is unreachable, when the caller wants a deterministic
    replay, or for assets without a deployed oracle (e.g. ETHSWP). The
    hedge command falls back to this automatically.
    """
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

    # r_ema: simple exponential smoothing with the same β so the spread
    # (r_ema − K2) stays well-defined for the wire-drift indicator.
    if samples:
        r_ema = samples[0].funding_rate
        for s in samples:
            r_ema = (1.0 - profile.k2_beta) * r_ema + profile.k2_beta * s.funding_rate
    else:
        r_ema = profile.fixed_leg_initial

    now_ms = int(time.time() * 1000)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000))
    return BTCSWPOracleSnapshot(
        source="replay",
        timestamp_iso=now_iso,
        timestamp_ms=now_ms,
        oracle_px=None,
        k_fixed_hr=k,
        r_ema_hr=r_ema,
        cfi=None,
    )


def fetch_btcswp_snapshot(
    profile: CFIAssetProfile = BTCSWP_PROFILE,
    *,
    timeout_s: float = 10.0,
) -> BTCSWPOracleSnapshot:
    """Live K2 with graceful fallback.

    Tries SEDA first; on any failure (network, 401, JSON parse, deployment
    gap) replays K2 from HL `fundingHistory`. Assets without a deployed
    oracle (e.g. ETHSWP, cfi_asset_index = -1) skip SEDA entirely.
    """
    if profile.cfi_asset_index < 0:
        return replay_k2_from_hl(profile, timeout_s=timeout_s)
    try:
        return fetch_seda_snapshot(profile, timeout_s=timeout_s)
    except Exception as e:
        log.warning(
            "seda_oracle: SEDA fetch failed (%s), falling back to HL replay",
            e,
        )
        return replay_k2_from_hl(profile, timeout_s=timeout_s)
