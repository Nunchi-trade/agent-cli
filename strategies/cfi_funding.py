"""CFI v2 funding inputs from Hyperliquid only."""
from __future__ import annotations
import logging, os, time
from dataclasses import dataclass
from typing import Literal, Optional
import requests
from strategies.cfi_hedge import BTCSWP_PROFILE, CFIAssetProfile, FundingRateSample, compute_k2_from_history
log = logging.getLogger(__name__)
HL_INFO_URL_DEFAULT = "https://api.hyperliquid.xyz/info"
def _hl_info_url() -> str: return os.environ.get("HL_INFO_URL", HL_INFO_URL_DEFAULT)
@dataclass(frozen=True)
class CfiFundingSnapshot:
    source: Literal["hl"]; timestamp_iso: str; timestamp_ms: int; oracle_px: Optional[float]
    k_fixed_hr: float; r_ema_hr: float; cfi: Optional[float]
def _fetch_funding_history(coin, lookback_hours, *, timeout_s=10.0):
    end_time = int(time.time()*1000); start_time = end_time - lookback_hours*3600000
    resp = requests.post(_hl_info_url(), json={"type":"fundingHistory","coin":coin,"startTime":start_time,"endTime":end_time}, timeout=timeout_s)
    resp.raise_for_status(); return resp.json() or []
def fetch_hl_current_funding_hr(coin, *, timeout_s=10.0):
    try:
        resp = requests.post(_hl_info_url(), json={"type":"metaAndAssetCtxs"}, timeout=timeout_s); resp.raise_for_status(); data = resp.json()
        for meta, ctx in zip(data[0]["universe"], data[1]):
            if meta.get("name")==coin: return float(ctx.get("funding",0))
        return None
    except Exception as e:
        log.warning("cfi_funding: fetch_hl_current_funding_hr(%s) failed: %s", coin, e); return None
def replay_k2_from_hl(profile=BTCSWP_PROFILE, *, lookback_hours=168, timeout_s=10.0):
    hist = _fetch_funding_history(profile.hl_coin, lookback_hours, timeout_s=timeout_s)
    samples = [FundingRateSample(funding_rate=float(e["fundingRate"]), time=int(e["time"])) for e in hist]
    k = compute_k2_from_history(samples, profile)
    if samples:
        r_ema = samples[0].funding_rate
        for s in samples: r_ema = (1-profile.k2_beta)*r_ema + profile.k2_beta*s.funding_rate
    else: r_ema = profile.fixed_leg_initial
    now_ms = int(time.time()*1000)
    return CfiFundingSnapshot(source="hl", timestamp_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms/1000)), timestamp_ms=now_ms, oracle_px=None, k_fixed_hr=k, r_ema_hr=r_ema, cfi=None)
def fetch_cfi_funding_snapshot(profile=BTCSWP_PROFILE, *, timeout_s=10.0): return replay_k2_from_hl(profile, timeout_s=timeout_s)
BTCSWPOracleSnapshot = CfiFundingSnapshot; fetch_btcswp_snapshot = fetch_cfi_funding_snapshot
