"""Pear campaign defaults shared by CLI commands."""
from __future__ import annotations

import os
from typing import Any, Dict

PEAR_BUILDER_ADDRESS = "0xA47D4d99191db54A4829cdf3de2417E527c3b042"
PEAR_BUILDER_FEE_TENTHS_BPS = 60  # 6 bps
PEAR_BTCSWP_ASSET = "BTCSWP"


def pear_builder_info() -> Dict[str, Any]:
    return {"b": PEAR_BUILDER_ADDRESS, "f": PEAR_BUILDER_FEE_TENTHS_BPS}


def pear_builder_fee_bps() -> float:
    return PEAR_BUILDER_FEE_TENTHS_BPS / 10.0


def pear_btcswp_asset() -> str:
    """Pear uses the HIP-3 registered symbol; allow deployment-specific override."""
    return os.getenv("PEAR_BTCSWP_ASSET", PEAR_BTCSWP_ASSET)
