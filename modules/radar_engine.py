"""OpportunityRadarEngine — pure, stateless radar (zero I/O).

All data passed in. Returns RadarResult. Fully testable.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from modules.radar_config import RadarConfig
from modules.radar_state import DisqualifiedAsset, Opportunity, RadarResult
from modules.radar_technicals import (
    analyze_4h_trend,
    calc_ema,
    calc_rsi,
    classify_hourly_trend,
    detect_patterns,
    price_changes,
    volume_ratio,
)


@dataclass
class AssetMeta:
    """Lightweight asset metadata from bulk screen."""
    name: str
    volume_24h: float
    funding_rate: float
    open_interest: float
    mark_price: float


class OpportunityRadarEngine:
    """Stateless radar engine. All data passed in — zero I/O."""

    def __init__(self, config: Optional[RadarConfig] = None):
        self.config = config or RadarConfig()

    def scan(
        self,
        all_markets: list,
        btc_candles_4h: List[Dict],
        btc_candles_1h: List[Dict],
        asset_candles: Dict[str, Dict[str, List[Dict]]],
        scan_history: List[Dict] = None,
    ) -> RadarResult:
        """Run the full 4-stage scan pipeline.

        Args:
            all_markets: [meta_dict, asset_ctxs_list] from HL API
            btc_candles_4h: 4h candles for BTC
            btc_candles_1h: 1h candles for BTC
            asset_candles: {asset_name: {"4h": [...], "1h": [...], "15m": [...]}}
            scan_history: list of previous scan result dicts
        """
        start_ms = int(time.time() * 1000)

        # Stage 0: BTC macro context
        btc_macro = self._btc_macro(btc_candles_4h, btc_candles_1h)

        # Stage 1: Bulk screen
        assets = self._bulk_screen(all_markets)

        # Stage 2: Select top N for deep dive
        top_assets = self._select_top(assets)

        # Stage 3: Deep dive each asset
        opportunities = []
        disqualified = []
        for asset in top_assets:
            candles = asset_candles.get(asset.name, {})
            c4h = candles.get("4h", [])
            c1h = candles.get("1h", [])
            c15m = candles.get("15m", [])

            if not c1h:
                continue

            # Try both directions, keep the best
            for direction in ["LONG", "SHORT"]:
                result = self._deep_dive(asset, c4h, c1h, c15m, btc_macro, direction)
                if isinstance(result, Opportunity):
                    opportunities.append(result)
                elif isinstance(result, DisqualifiedAsset):
                    disqualified.append(result)

        # Deduplicate: keep best direction per asset
        best_per_asset: Dict[str, Opportunity] = {}
        for opp in opportunities:
            existing = best_per_asset.get(opp.asset)
            if existing is None or opp.final_score > existing.final_score:
                best_per_asset[opp.asset] = opp
        opportunities = list(best_per_asset.values())

        # Stage 4: Apply momentum and sort
        opportunities = self._apply_momentum(opportunities, scan_history or [])

        # Filter by score threshold
        qualified = [o for o in opportunities if o.final_score >= self.config.score_threshold]

        elapsed_ms = int(time.time() * 1000) - start_ms
        return RadarResult(
            scan_time_ms=start_ms,
            btc_macro=btc_macro,
            opportunities=qualified,
            disqualified=disqualified,
            stats={
                "assets_scanned": len(all_markets[1]) if len(all_markets) > 1 else 0,
                "passed_stage1": len(assets),
                "deep_dived": len(top_assets),
                "qualified": len(qualified),
                "disqualified_count": len(disqualified),
                "scan_duration_ms": elapsed_ms,
            },
        )

    def _btc_macro(
        self, btc_4h: List[Dict], btc_1h: List[Dict],
    ) -> Dict[str, Any]:
        """Stage 0: Compute BTC macro context."""
        if not btc_4h or not btc_1h:
            return {
                "trend": "neutral", "strength": 0,
                "diff_pct": 0.0, "chg1h": 0.0,
                "modifiers": self.config.macro_modifiers.get("neutral", {}),
            }

        closes_4h = [float(c["c"]) for c in btc_4h]
        ema5 = calc_ema(closes_4h, 5)
        ema13 = calc_ema(closes_4h, 13)

        diff_pct = 0.0
        if ema5 and ema13 and ema13[-1] != 0:
            diff_pct = (ema5[-1] - ema13[-1]) / ema13[-1] * 100

        # 1h change
        closes_1h = [float(c["c"]) for c in btc_1h]
        chg1h = 0.0
        if len(closes_1h) >= 2 and closes_1h[-2] != 0:
            chg1h = (closes_1h[-1] - closes_1h[-2]) / closes_1h[-2] * 100

        # Classify
        if diff_pct > 1.0:
            trend = "strong_up"
        elif diff_pct > 0.2:
            trend = "up"
        elif diff_pct < -1.0:
            trend = "strong_down"
        elif diff_pct < -0.2:
            trend = "down"
        else:
            trend = "neutral"

        strength = min(int(abs(diff_pct) * 20), 100)

        return {
            "trend": trend,
            "strength": strength,
            "ema5": round(ema5[-1], 2) if ema5 else 0,
            "ema13": round(ema13[-1], 2) if ema13 else 0,
            "diff_pct": round(diff_pct, 3),
            "chg1h": round(chg1h, 3),
            "modifiers": self.config.macro_modifiers.get(trend, {}),
        }

    def _bulk_screen(self, all_markets: list) -> List[AssetMeta]:
        """Stage 1: Filter assets by minimum volume."""
        if len(all_markets) < 2:
            return []

        meta_info = all_markets[0]
        asset_ctxs = all_markets[1]
        universe = meta_info.get("universe", [])

        assets = []
        for i, ctx in enumerate(asset_ctxs):
            if i >= len(universe):
                break
            try:
                name = universe[i].get("name", "")
            except (IndexError, AttributeError):
                continue
            vol = float(ctx.get("dayNtlVlm", 0))
            if vol < self.config.min_volume_24h:
                continue

            assets.append(AssetMeta(
                name=name,
                volume_24h=vol,
                funding_rate=float(ctx.get("funding", 0)),
                open_interest=float(ctx.get("openInterest", 0)),
                mark_price=float(ctx.get("markPx", 0)),
            ))

        return assets

    def _select_top(self, assets: List[AssetMeta]) -> List[AssetMeta]:
        """Stage 2: Sort by composite liquidity score, take top N."""
        for a in assets:
            a._sort_key = a.volume_24h * math.sqrt(max(a.open_interest, 1))  # type: ignore[attr-defined]

        assets.sort(key=lambda a: a._sort_key, reverse=True)  # type: ignore[attr-defined]
        return assets[:self.config.top_n_deep]

    def _deep_dive(
        self,
        asset: AssetMeta,
        candles_4h: List[Dict],
        candles_1h: List[Dict],
        candles_15m: List[Dict],
        btc_macro: Dict[str, Any],
        direction: str,
    ) -> Opportunity | DisqualifiedAsset:
        """Stage 3: Deep technical analysis of a single asset in one direction."""

        # Compute technicals
        closes_1h = [float(c["c"]) for c in candles_1h] if candles_1h else []
        closes_15m = [float(c["c"]) for c in candles_15m] if candles_15m else []

        hourly_trend = classify_hourly_trend(candles_1h)
        trend_4h, trend_4h_strength = analyze_4h_trend(candles_4h)
        rsi_1h = calc_rsi(closes_1h) if len(closes_1h) >= 15 else 50.0
        rsi_15m = calc_rsi(closes_15m) if len(closes_15m) >= 15 else 50.0
        vol_ratio_1h = volume_ratio(candles_1h)
        vol_ratio_15m = volume_ratio(candles_15m) if candles_15m else 1.0
        patterns = detect_patterns(candles_1h)
        changes = price_changes(candles_1h)

        # Funding annualized (assuming 8h funding period)
        funding_ann_pct = abs(asset.funding_rate) * 3 * 365 * 100

        # BTC macro modifier for this direction
        modifiers = btc_macro.get("modifiers", {})
        macro_mod = modifiers.get(direction, 0)

        technicals_dict = {
            "rsi1h": round(rsi_1h, 1),
            "rsi15m": round(rsi_15m, 1),
            "hourly_trend": hourly_trend,
            "trend_4h": trend_4h,
            "trend_4h_strength": trend_4h_strength,
            "patterns": patterns,
            "vol_ratio_1h": round(vol_ratio_1h, 2),
            "vol_ratio_15m": round(vol_ratio_15m, 2),
            **changes,
        }

        market_data_dict = {
            "vol24h": asset.volume_24h,
            "oi": asset.open_interest,
            "funding_rate": asset.funding_rate,
            "funding_ann_pct": round(funding_ann_pct, 1),
            "mark_price": asset.mark_price,
        }

        # --- Hard disqualifiers ---
        thresholds = self.config.disqualify_thresholds

        # 1. Counter-trend on hourly
        if direction == "LONG" and hourly_trend == "DOWN":
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="counter_trend_hourly",
                details={"hourly_trend": hourly_trend},
            )
        if direction == "SHORT" and hourly_trend == "UP":
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="counter_trend_hourly",
                details={"hourly_trend": hourly_trend},
            )

        # 2. Extreme RSI
        if direction == "LONG" and rsi_1h > thresholds.get("extreme_rsi_long", 80):
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="extreme_rsi",
                details={"rsi1h": rsi_1h},
            )
        if direction == "SHORT" and rsi_1h < thresholds.get("extreme_rsi_short", 20):
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="extreme_rsi",
                details={"rsi1h": rsi_1h},
            )

        # 3. Counter-trend 4h with high strength
        ct_4h_thresh = thresholds.get("counter_trend_4h_strength", 50)
        if direction == "LONG" and trend_4h in ("down", "strong_down") and trend_4h_strength > ct_4h_thresh:
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="counter_trend_4h_strong",
                details={"trend_4h": trend_4h, "strength": trend_4h_strength},
            )
        if direction == "SHORT" and trend_4h in ("up", "strong_up") and trend_4h_strength > ct_4h_thresh:
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="counter_trend_4h_strong",
                details={"trend_4h": trend_4h, "strength": trend_4h_strength},
            )

        # 4. Volume dying (both TFs < threshold)
        vol_dying = thresholds.get("volume_dying_ratio", 0.5)
        if vol_ratio_1h < vol_dying and vol_ratio_15m < vol_dying:
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="volume_dying",
                details={"vol_ratio_1h": vol_ratio_1h, "vol_ratio_15m": vol_ratio_15m},
            )

        # 5. Heavy unfavorable funding
        heavy_funding = thresholds.get("heavy_funding_annualized_pct", 50.0)
        funding_unfavorable = (
            (direction == "LONG" and asset.funding_rate > 0 and funding_ann_pct > heavy_funding) or
            (direction == "SHORT" and asset.funding_rate < 0 and funding_ann_pct > heavy_funding)
        )
        if funding_unfavorable:
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="heavy_unfavorable_funding",
                details={"funding_ann_pct": funding_ann_pct},
            )

        # 6. BTC macro headwind
        headwind_thresh = thresholds.get("btc_headwind_modifier", -30)
        if macro_mod <= headwind_thresh:
            return DisqualifiedAsset(
                asset=asset.name, direction=direction,
                reason="btc_macro_headwind",
                details={"macro_modifier": macro_mod, "btc_trend": btc_macro.get("trend")},
            )

        # --- Score 3 pillars ---
        risks = []

        # Market Structure (0-100)
        ms_score = self._score_market_structure(asset, vol_ratio_1h, risks)

        # Technicals (0-100)
        tech_score = self._score_technicals(
            direction, hourly_trend, trend_4h, trend_4h_strength,
            rsi_1h, rsi_15m, vol_ratio_1h, vol_ratio_15m, patterns, changes, risks,
        )

        # Funding (0-100)
        fund_score = self._score_funding(direction, asset.funding_rate, funding_ann_pct, risks)

        pillar_scores = {
            "market_structure": round(ms_score, 1),
            "technicals": round(tech_score, 1),
            "funding": round(fund_score, 1),
        }

        # Weighted raw score (0-400 scale)
        w = self.config.pillar_weights
        raw = (
            ms_score * w.get("market_structure", 0.35) +
            tech_score * w.get("technicals", 0.40) +
            fund_score * w.get("funding", 0.25)
        ) * 4

        final = raw + macro_mod

        return Opportunity(
            asset=asset.name,
            direction=direction,
            final_score=round(final, 1),
            raw_score=round(raw, 1),
            macro_modifier=macro_mod,
            pillar_scores=pillar_scores,
            technicals=technicals_dict,
            market_data=market_data_dict,
            risks=risks,
        )

    def _score_market_structure(
        self, asset: AssetMeta, vol_ratio: float, risks: List[str],
    ) -> float:
        """Score market structure pillar (0-100)."""
        score = 0.0

        # Volume tiers
        if asset.volume_24h > 50_000_000:
            score += 30
        elif asset.volume_24h > 10_000_000:
            score += 20
        elif asset.volume_24h > 1_000_000:
            score += 10

        # Volume surge
        if vol_ratio > 2.0:
            score += 30
        elif vol_ratio > 1.3:
            score += 20
        elif vol_ratio > 1.0:
            score += 10
        else:
            risks.append("declining_volume")

        # OI tiers
        if asset.open_interest > 10_000_000:
            score += 20
        elif asset.open_interest > 1_000_000:
            score += 10

        # OI / Volume health ratio
        if asset.volume_24h > 0:
            oi_vol = asset.open_interest / asset.volume_24h
            if 0.3 <= oi_vol <= 3.0:
                score += 20
            else:
                risks.append("oi_volume_imbalance")

        return min(score, 100)

    def _score_technicals(
        self,
        direction: str,
        hourly_trend: str,
        trend_4h: str,
        trend_4h_strength: int,
        rsi_1h: float,
        rsi_15m: float,
        vol_ratio_1h: float,
        vol_ratio_15m: float,
        patterns: List[str],
        changes: Dict[str, float],
        risks: List[str],
    ) -> float:
        """Score technicals pillar (0-100)."""
        score = 0.0

        # 4h trend alignment (0-20)
        if direction == "LONG" and trend_4h in ("up", "strong_up"):
            score += min(20, 10 + trend_4h_strength // 10)
        elif direction == "SHORT" and trend_4h in ("down", "strong_down"):
            score += min(20, 10 + trend_4h_strength // 10)
        elif direction == "LONG" and trend_4h in ("down", "strong_down"):
            score -= 5
            risks.append("4h_counter_trend")
        elif direction == "SHORT" and trend_4h in ("up", "strong_up"):
            score -= 5
            risks.append("4h_counter_trend")

        # Hourly trend alignment (0-20)
        if direction == "LONG" and hourly_trend == "UP":
            score += 20
        elif direction == "SHORT" and hourly_trend == "DOWN":
            score += 20
        elif hourly_trend == "NEUTRAL":
            score += 5
        else:
            score -= 30
            risks.append("hourly_counter_trend")

        # RSI (0-20)
        if direction == "LONG":
            if rsi_1h < 35:
                score += 20  # oversold = great long entry
            elif rsi_1h < 50:
                score += 15
            elif rsi_1h < 65:
                score += 5
            else:
                risks.append("rsi_elevated")
        else:  # SHORT
            if rsi_1h > 65:
                score += 20  # overbought = great short entry
            elif rsi_1h > 50:
                score += 15
            elif rsi_1h > 35:
                score += 5
            else:
                risks.append("rsi_depressed")

        # RSI multi-TF convergence (0-10)
        if direction == "LONG" and rsi_1h < 40 and rsi_15m < 40:
            score += 10
        elif direction == "SHORT" and rsi_1h > 60 and rsi_15m > 60:
            score += 10
        elif (direction == "LONG" and rsi_1h < 50 and rsi_15m < 50) or \
             (direction == "SHORT" and rsi_1h > 50 and rsi_15m > 50):
            score += 5

        # Volume confirmation (0-15)
        avg_vol = (vol_ratio_1h + vol_ratio_15m) / 2
        if avg_vol > 1.5:
            score += 15
        elif avg_vol > 1.0:
            score += 10
        elif avg_vol > 0.7:
            score += 5

        # Candlestick patterns (0-15)
        bullish = {"hammer", "bullish_engulfing", "three_soldiers"}
        bearish = {"bearish_engulfing", "three_crows"}
        pattern_set = set(patterns)
        if direction == "LONG":
            matched = pattern_set & bullish
            score += min(15, len(matched) * 8)
        else:
            matched = pattern_set & bearish
            score += min(15, len(matched) * 8)
        if "doji" in pattern_set:
            score += 3  # indecision, slight plus for reversal

        # Momentum alignment (0-10)
        chg = changes.get("chg4h", 0)
        if direction == "LONG" and chg > 0:
            score += min(10, int(chg * 3))
        elif direction == "SHORT" and chg < 0:
            score += min(10, int(abs(chg) * 3))

        return max(score, 0)

    def _score_funding(
        self,
        direction: str,
        funding_rate: float,
        funding_ann_pct: float,
        risks: List[str],
    ) -> float:
        """Score funding pillar (0-100)."""
        score = 0.0

        # Neutral funding (near zero)
        if funding_ann_pct < 5.0:
            score += 40
        elif funding_ann_pct < 15.0:
            # Favorable: getting paid to hold
            is_favorable = (
                (direction == "LONG" and funding_rate < 0) or
                (direction == "SHORT" and funding_rate > 0)
            )
            if is_favorable:
                score += 35
            else:
                score += 20
        else:
            # Extreme funding
            is_favorable = (
                (direction == "LONG" and funding_rate < 0) or
                (direction == "SHORT" and funding_rate > 0)
            )
            if is_favorable:
                # Extreme favorable — potential squeeze setup
                score += 35
            elif funding_ann_pct > 30:
                score -= 20
                risks.append("heavy_unfavorable_funding")
            else:
                score -= 10
                risks.append("unfavorable_funding")

        return max(score, 0)

    def _apply_momentum(
        self, opportunities: List[Opportunity], scan_history: List[Dict],
    ) -> List[Opportunity]:
        """Stage 4: Apply cross-scan momentum and sort."""
        if not scan_history:
            opportunities.sort(key=lambda o: o.final_score, reverse=True)
            return opportunities

        for opp in opportunities:
            # Find in previous scans
            prev_scores = []
            streak = 0
            for scan in reversed(scan_history):
                found = False
                for prev_opp in scan.get("opportunities", []):
                    if prev_opp.get("asset") == opp.asset:
                        prev_scores.append(prev_opp.get("final_score", 0))
                        found = True
                        break
                if found:
                    streak += 1
                else:
                    break

            score_delta = opp.final_score - prev_scores[0] if prev_scores else 0.0
            opp.momentum = {
                "score_delta": round(score_delta, 1),
                "scan_streak": streak,
            }

        opportunities.sort(key=lambda o: o.final_score, reverse=True)
        return opportunities
