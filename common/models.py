"""Shared Pydantic models for the trading system."""
from __future__ import annotations
import os
from typing import Any, Collection, Dict, List, Optional, Set
from pydantic import BaseModel, Field
DEFAULT_SUFFIX = "-PERP"
BTCSWP_ASSET = "BTCSWP"
HIP3_DEXS = {"yex": {"coin_prefix": "yex:", "instrument_suffix": "-USDYP", "assets": frozenset({"VXX", "US3M", BTCSWP_ASSET})}, "para": {"coin_prefix": "para:", "instrument_suffix": "-PARA", "assets": frozenset({BTCSWP_ASSET})}}
SINGLE_DEX_ASSETS = frozenset({"VXX", "US3M"})
SPECIAL_ASSETS, HL_COIN_PREFIXES, DEX_BY_SUFFIX = {}, {}, {}
for _dex_id, _dex in HIP3_DEXS.items():
    HL_COIN_PREFIXES[_dex["instrument_suffix"]] = _dex["coin_prefix"]
    DEX_BY_SUFFIX[_dex["instrument_suffix"]] = _dex_id
    for _asset in _dex["assets"]:
        if _asset in SINGLE_DEX_ASSETS: SPECIAL_ASSETS[_asset] = _dex["instrument_suffix"]
INSTRUMENT_SUFFIXES = tuple(sorted({DEFAULT_SUFFIX} | set(HL_COIN_PREFIXES.keys()), key=len, reverse=True))
def is_mainnet(mainnet=None):
    if mainnet is not None: return mainnet
    return os.environ.get("HL_TESTNET", "true").lower() == "false"
def active_hip3_dex_ids(mainnet=None): return ["para"] if is_mainnet(mainnet) else ["yex"]
def normalize_hl_coin(coin):
    if ":" not in coin: return coin
    p,a=coin.split(":",1); return f"{p}:{a.upper()}"
def hl_coin_to_asset(coin):
    return coin.split(":",1)[1].upper() if ":" in coin else coin.upper()
def _btcswp_hl_coin(mainnet=None):
    d="para" if is_mainnet(mainnet) else "yex"; return f"{HIP3_DEXS[d]['coin_prefix']}{BTCSWP_ASSET}"
def asset_to_instrument(asset, mainnet=None):
    u=asset.upper()
    if u==BTCSWP_ASSET: return u + (HIP3_DEXS["para"]["instrument_suffix"] if is_mainnet(mainnet) else HIP3_DEXS["yex"]["instrument_suffix"])
    return u + SPECIAL_ASSETS.get(u, DEFAULT_SUFFIX)
def instrument_to_coin(instrument, mainnet=None):
    u=instrument.upper()
    if ":" in instrument: return normalize_hl_coin(instrument)
    for suffix in INSTRUMENT_SUFFIXES:
        if u.endswith(suffix):
            asset=instrument[:-len(suffix)]
            if suffix==DEFAULT_SUFFIX: return asset
            if asset.upper()==BTCSWP_ASSET and suffix=="-USDYP": return _btcswp_hl_coin(mainnet)
            return f"{HL_COIN_PREFIXES[suffix]}{asset.upper()}"
    return instrument
def instrument_to_asset(instrument):
    u=instrument.upper()
    for suffix in INSTRUMENT_SUFFIXES:
        if u.endswith(suffix): return instrument[:-len(suffix)]
    return hl_coin_to_asset(instrument) if ":" in instrument else instrument
def coin_to_instrument(coin, mainnet=None):
    n=normalize_hl_coin(coin)
    for suffix,prefix in HL_COIN_PREFIXES.items():
        if n.startswith(prefix):
            asset=n[len(prefix):]
            if asset.upper()==BTCSWP_ASSET: return asset_to_instrument(BTCSWP_ASSET, mainnet=mainnet)
            return asset+suffix
    return asset_to_instrument(n, mainnet=mainnet)
def asset_to_coin(asset, mainnet=None):
    u=asset.upper()
    if u==BTCSWP_ASSET: return _btcswp_hl_coin(mainnet)
    suffix=SPECIAL_ASSETS.get(u, DEFAULT_SUFFIX); prefix=HL_COIN_PREFIXES.get(suffix, ""); return prefix+u if prefix else u
def asset_matches_allowed(asset, allowed):
    u=asset.upper(); au={a.upper() for a in allowed}
    return u in au or any(u+suffix in au for suffix in INSTRUMENT_SUFFIXES)
def dex_for_instrument(instrument, mainnet=None):
    u=instrument.upper()
    if u.endswith("-PARA"): return "para"
    if u.endswith("-USDYP"):
        if instrument[:-len("-USDYP")].upper()==BTCSWP_ASSET: return "para" if is_mainnet(mainnet) else "yex"
        return "yex"
    if ":" in instrument:
        p=instrument.split(":",1)[0]
        if p in HIP3_DEXS: return p
    for suffix,dex_id in DEX_BY_SUFFIX.items():
        if instrument.endswith(suffix): return dex_id
    return None
def get_hip3_dex_ids(instruments, mainnet=None):
    return {d for inst in instruments for d in [dex_for_instrument(inst, mainnet=mainnet)] if d}
class MarketSnapshot(BaseModel):
    instrument: str = "ETH-PERP"; mid_price: float = 0.0; bid: float = 0.0; ask: float = 0.0; spread_bps: float = 0.0; timestamp_ms: int = 0; volume_24h: float = 0.0; funding_rate: float = 0.0; open_interest: float = 0.0
class VerifyResult(BaseModel):
    ok: bool; checks: Dict[str, bool] = Field(default_factory=dict); errors: List[str] = Field(default_factory=list)
class StrategyDecision(BaseModel):
    action: str = "noop"; instrument: str = "ETH-PERP"; side: str = ""; size: float = 0.0; limit_price: float = 0.0; order_type: str = "Gtc"; meta: Dict[str, Any] = Field(default_factory=dict)
class Decision(BaseModel):
    decision_id: str; strategy_id: str = ""; action: str = "limit_order"; instrument: str = "ETH"; side: Optional[str] = None; size: float = 0.0; limit_price: float = 0.0; timestamp_ms: int = 0
