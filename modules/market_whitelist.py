"""MarketWhitelist — shared filter for restricting scanners to a subset of HL perps.

Used by RADAR and PULSE to focus on a configured asset universe (e.g. TradeXYZ
HIP-3 commodity / index perps for the HOUSE-Jump preset).

Patterns:
    - Empty list = pass through (no filtering, scan all assets)
    - Exact name match: ``xyz:GOLD``
    - Glob pattern: ``xyz:*`` matches any TradeXYZ HIP-3 asset
    - Comma-separated env var: ``MARKET_WHITELIST=xyz:GOLD,xyz:CL,xyz:SILVER``
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class MarketWhitelist:
    """Glob-aware asset filter."""

    patterns: List[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """True when the whitelist is non-empty and should restrict the universe."""
        return bool(self.patterns)

    def matches(self, asset_name: str) -> bool:
        """Return True when the asset is allowed.

        Empty whitelist = allow everything (back-compat). Otherwise the asset
        must match at least one pattern via shell-style glob (``fnmatch``).
        """
        if not self.patterns:
            return True
        return any(fnmatch.fnmatchcase(asset_name, p) for p in self.patterns)

    @classmethod
    def from_env(cls, env_var: str = "MARKET_WHITELIST") -> "MarketWhitelist":
        """Parse comma-separated patterns from an env var. Whitespace tolerant."""
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            return cls()
        patterns = [p.strip() for p in raw.split(",") if p.strip()]
        return cls(patterns=patterns)

    @classmethod
    def from_list(cls, patterns: List[str]) -> "MarketWhitelist":
        """Build from an explicit list (e.g. preset YAML)."""
        return cls(patterns=[p for p in patterns if p])
