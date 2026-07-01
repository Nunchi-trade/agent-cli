"""CFI v2 funding-rate hedge math.

Pure functions. No network, no side effects. Mirrors
`~/demo-ide/src/lib/cfi-hedge.ts` so the CLI and the demo-ide tile
produce identical proposals for a given (position, funding, K2) tuple.

Source identity (the reason the math works):

    Long BTC perp pays:        Π_fund  = -N_perp × ∫f̃ ds            (volatile floating)
    Long CFI v2 perp receives: Π_hedge = N_hedge × ∫(f̃ - k̃) ds      (excess funding, internally levered by L)
    Combined (with N_hedge = N_perp / L):
                                Π_net   = -N_perp × ∫k̃ ds            (K2 fixed leg ONLY)

The wire-price formula P = B + S_eff × ∫(f - K2) dt has S_eff = scale_s × L,
which embeds the leverage L into the CFI v2 perp's payoff — that's why a
$X/L hedge fully cancels funding-rate exposure on a $X perp.

Hourly annualisation uses 8760 hours/year. HL's `metaAndAssetCtxs.funding`
field is the predicted hourly rate (HL transitioned away from 8h
settlements in 2024). The deprecated 8h × 3 × 365 = 1095 multiplier is
NOT used here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

from common.models import BTCSWP_ASSET, asset_to_coin, asset_to_instrument, is_mainnet

HOURS_PER_YEAR = 8_760


# ─── Asset profiles (deployed BTCSWP parameters) ────────────────────────────
# Source: ~/UI-BTCSWP-Fixed/docs/parameters.md (deployed on YEX testnet).


@dataclass(frozen=True)
class CFIAssetProfile:
    """Deployed-market parameters for one CFI v2 perp."""

    name: str
    """Asset symbol shown in the UI."""
    vol_mult_l: int
    """Volatility multiplier — hedge ratio = 1/L."""
    fixed_leg_initial: float
    """Seed value for K2 EMA (hourly fraction). Used until oracle is reachable."""
    k2_beta: float
    """EMA decay factor. 0.080042 ≈ 12-hour time constant for BTC; 0.005952381 ≈ 7-day."""
    baseline_b0: float
    """Baseline wire price."""
    scale_s: float
    """Price sensitivity base. Effective sensitivity is scale_s × L."""
    hl_coin: str
    """Hyperliquid coin ticker for the upstream funding rate."""
    cfi_asset_name: str
    """YEX HIP-3 asset name for the deployed CFI v2 perp (e.g. 'yex:BTCSWP')."""
    cfi_instrument: str
    """nunchi-cli instrument label (e.g. 'BTCSWP-USDYP') — passes through `resolve_instrument`."""
    cfi_asset_index: int
    """YEX-local asset index. -1 = not deployed (sentinel)."""


def btcswp_profile(mainnet: Optional[bool] = None) -> CFIAssetProfile:
    """Network-aware BTCSWP CFI v2 profile.

    Testnet: yex:BTCSWP / BTCSWP-USDYP (YEX yield perp).
    Mainnet: para:BTCSWP / BTCSWP-PARA (Paragon swap perp).
    """
    on_mainnet = is_mainnet(mainnet)
    return CFIAssetProfile(
        name="BTC",
        vol_mult_l=15,
        fixed_leg_initial=0.0000029,
        k2_beta=0.080042,
        baseline_b0=75_000,
        scale_s=1_000_000,
        hl_coin="BTC",
        cfi_asset_name=asset_to_coin(BTCSWP_ASSET, mainnet=mainnet),
        cfi_instrument=asset_to_instrument(BTCSWP_ASSET, mainnet=mainnet),
        cfi_asset_index=2 if not on_mainnet else -1,
    )


# Back-compat alias — testnet profile (YEX).
BTCSWP_PROFILE = btcswp_profile(mainnet=False)

# ETHSWP placeholder — not yet deployed. Filled-in `vol_mult_l` etc. match
# ~/hyperliquid-funding-rate-perps/tools/hedge_calculator.py.
ETHSWP_PROFILE = CFIAssetProfile(
    name="ETH",
    vol_mult_l=5,
    fixed_leg_initial=0.0000069,
    k2_beta=0.005952381,
    baseline_b0=3_000,
    scale_s=1_000_000,
    hl_coin="ETH",
    cfi_asset_name="yex:ETHSWP",
    cfi_instrument="ETHSWP-USDYP",
    cfi_asset_index=-1,
)

CFI_PROFILES = {
    "ETH": ETHSWP_PROFILE,
}


def get_cfi_profile(
    coin: str,
    mainnet: Optional[bool] = None,
    hedge_instrument: Optional[str] = None,
) -> Optional[CFIAssetProfile]:
    """Look up a deployed profile by coin ticker. Returns None for unknowns."""
    upper = coin.upper()
    if upper in ("BTC", BTCSWP_ASSET):
        base = btcswp_profile(mainnet=mainnet)
        if hedge_instrument is None:
            return base
        from cli.strategy_registry import resolve_instrument
        from common.models import instrument_to_coin

        resolved = resolve_instrument(hedge_instrument, mainnet=mainnet)
        cfi_coin = instrument_to_coin(resolved, mainnet=mainnet)
        return replace(base, cfi_asset_name=cfi_coin, cfi_instrument=resolved)
    return CFI_PROFILES.get(upper)


# ─── Rate conversions ───────────────────────────────────────────────────────


def hourly_to_apy(hourly_rate: float) -> float:
    """Hourly fraction → annualised fraction (× 8760)."""
    return hourly_rate * HOURS_PER_YEAR


def hourly_to_daily(hourly_rate: float) -> float:
    return hourly_rate * 24


def apy_to_hourly(apy: float) -> float:
    return apy / HOURS_PER_YEAR


# ─── K2 EMA replay (fallback when SEDA oracle is unreachable) ───────────────


@dataclass(frozen=True)
class FundingRateSample:
    """One historical hourly funding rate observation."""

    funding_rate: float
    time: int  # ms epoch


def compute_k2_from_history(
    samples: Iterable[FundingRateSample],
    profile: CFIAssetProfile,
) -> float:
    """Replay the K2 EMA recurrence from a sequence of historical rates.

    Matches the deployed oracle's recurrence exactly:

        k_fixed = (1 - β) × k_fixed + β × current_rate

    Returns the K2 hourly fraction after the last sample is folded in.
    """
    k = profile.fixed_leg_initial
    for s in samples:
        k = (1.0 - profile.k2_beta) * k + profile.k2_beta * s.funding_rate
    return k


def k2_time_constant_hours(profile: CFIAssetProfile) -> float:
    """Approximate time constant of the K2 EMA in hours.

        β = 1 - exp(-1/τ)  ⇒  τ = -1 / ln(1 - β)
    """
    import math
    return -1.0 / math.log(1.0 - profile.k2_beta)


# ─── Hedge proposal ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HLPositionSummary:
    """Subset of a Hyperliquid clearinghouseState position we care about."""

    coin: str
    side: str  # "long" | "short"
    size_coin: float
    entry_px: float
    mark_px: float
    notional_usd: float
    leverage: int
    unrealized_pnl_usd: float
    cum_funding_usd: float


@dataclass(frozen=True)
class CFIHedgeLeg:
    venue: str  # "Hyperliquid" | "YEX-HIP3"
    side: str  # "long" | "short"
    market: str
    notional_usd: float
    size_coin: Optional[float]
    rate_apy: float
    role: str  # "existing" | "hedge"


@dataclass(frozen=True)
class CFIHorizon:
    label: str
    hours: int
    unhedged_usd: float
    hedged_usd: float
    savings_usd: float


@dataclass(frozen=True)
class CFIHedgeProposal:
    """A complete hedge proposal — sizing, rates, projections."""

    user_address: str
    profile: CFIAssetProfile
    source_position: HLPositionSummary
    current_funding_hr: float
    k_fixed_hr: float
    k_fixed_apy: float
    current_funding_apy: float
    excess_apy: float
    wire_drift_per_hour_usd: float
    hedge_ratio: float  # 1/L
    hedge_notional_usd: float
    total_capital_usd: float
    capital_efficiency: float  # perp / (perp + hedge)
    legs: tuple  # (existing_leg, hedge_leg)
    projections: tuple  # tuple of CFIHorizon
    created_at_ms: int


DEFAULT_HORIZONS = (
    ("1 day", 24),
    ("1 week", 168),
    ("1 month", 720),
    ("3 months", 2_160),
    ("6 months", 4_380),
    ("1 year", 8_760),
)


def build_cfi_hedge_proposal(
    *,
    user_address: str,
    position: HLPositionSummary,
    current_funding_hr: float,
    k_fixed_hr: float,
    horizons: Optional[Iterable[tuple]] = None,
    now_ms: Optional[int] = None,
    mainnet: Optional[bool] = None,
    hedge_instrument: Optional[str] = None,
) -> Optional[CFIHedgeProposal]:
    """Build a CFI v2 hedge proposal for an existing HL perp position.

    Returns None if no CFI profile is deployed for the position's coin.
    """
    profile = get_cfi_profile(
        position.coin,
        mainnet=mainnet,
        hedge_instrument=hedge_instrument,
    )
    if profile is None:
        return None

    s_eff = profile.scale_s * profile.vol_mult_l
    excess_hr = current_funding_hr - k_fixed_hr

    hedge_ratio = 1.0 / profile.vol_mult_l
    hedge_notional = position.notional_usd * hedge_ratio
    total_capital = position.notional_usd + hedge_notional
    capital_efficiency = position.notional_usd / total_capital

    existing_leg = CFIHedgeLeg(
        venue="Hyperliquid",
        side=position.side,
        market=f"{position.coin}-PERP",
        notional_usd=position.notional_usd,
        size_coin=position.size_coin,
        rate_apy=hourly_to_apy(current_funding_hr),
        role="existing",
    )
    hedge_leg = CFIHedgeLeg(
        venue="PARA-HIP3" if is_mainnet(mainnet) else "YEX-HIP3",
        # CFI v2 long pays you (funding − K2) per unit time. Same-side hedge
        # by the identity derivation: existing long → CFI long; existing
        # short → CFI short.
        side=position.side,
        market=profile.cfi_asset_name,
        notional_usd=hedge_notional,
        size_coin=None,  # sized in CFI v2 units at execute time
        rate_apy=hourly_to_apy(k_fixed_hr),
        role="hedge",
    )

    horizon_iter = horizons if horizons is not None else DEFAULT_HORIZONS
    projections = tuple(
        CFIHorizon(
            label=label,
            hours=hours,
            unhedged_usd=position.notional_usd * current_funding_hr * hours,
            hedged_usd=position.notional_usd * k_fixed_hr * hours,
            savings_usd=position.notional_usd * (current_funding_hr - k_fixed_hr) * hours,
        )
        for (label, hours) in horizon_iter
    )

    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)

    return CFIHedgeProposal(
        user_address=user_address,
        profile=profile,
        source_position=position,
        current_funding_hr=current_funding_hr,
        k_fixed_hr=k_fixed_hr,
        k_fixed_apy=hourly_to_apy(k_fixed_hr),
        current_funding_apy=hourly_to_apy(current_funding_hr),
        excess_apy=hourly_to_apy(excess_hr),
        wire_drift_per_hour_usd=s_eff * excess_hr,
        hedge_ratio=hedge_ratio,
        hedge_notional_usd=hedge_notional,
        total_capital_usd=total_capital,
        capital_efficiency=capital_efficiency,
        legs=(existing_leg, hedge_leg),
        projections=projections,
        created_at_ms=now_ms,
    )


# ─── Display formatting ─────────────────────────────────────────────────────


def format_apy_pct(apy_fraction: float, decimals: int = 2) -> str:
    sign = "+" if apy_fraction >= 0 else ""
    return f"{sign}{apy_fraction * 100:.{decimals}f}%"


def format_usd(amount: float, decimals: int = 0) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.{decimals}f}"


def format_usd_signed(amount: float, decimals: int = 0) -> str:
    sign = "+" if amount >= 0 else "-"
    return f"{sign}${abs(amount):,.{decimals}f}"


# ─── Live maintenance math ──────────────────────────────────────────────────

MS_PER_YEAR = 365 * 24 * 60 * 60 * 1_000


def compute_hedge_drift(current_annual: float, locked_annual: float) -> float:
    """Drift of the floating source rate away from the locked rate."""
    return current_annual - locked_annual


def accumulate_savings(
    prior_savings_usd: float,
    current_annual: float,
    locked_annual: float,
    notional_usd: float,
    dt_ms: int,
) -> float:
    """Integrate one more increment of savings over a `dt_ms` interval.

    Positive when the unhedged rate is above the locked rate (user avoided
    paying the higher floating rate).
    """
    if dt_ms <= 0:
        return prior_savings_usd
    drift = compute_hedge_drift(current_annual, locked_annual)
    increment = notional_usd * drift * (dt_ms / MS_PER_YEAR)
    return prior_savings_usd + increment


def format_duration(ms: int) -> str:
    """Human-readable duration ('3m 42s', '1h 12m', '2d 4h')."""
    if not isinstance(ms, (int, float)) or ms < 0:
        return "0s"
    total_sec = int(ms / 1000)
    days = total_sec // 86_400
    hours = (total_sec % 86_400) // 3_600
    minutes = (total_sec % 3_600) // 60
    seconds = total_sec % 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
