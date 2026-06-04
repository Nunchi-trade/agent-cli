"""Onboarding / readiness checks — backend truth for "can this institution
get to first attributed fill?".

This module owns the per-check pass/fail/action_needed/na logic so it can be
unit-tested without touching the CLI surface. `setup status` in
cli/commands/setup.py is a thin wrapper that renders the result of
build_readiness_report().

Each check is a dict:
    {"id": str, "status": "pass"|"fail"|"action_needed"|"na", "detail": str}

Status semantics:
    pass          — requirement satisfied, nothing to do.
    fail          — requirement unmet and the user/agent can fix it locally
                    (e.g. install a package, configure a wallet).
    action_needed — requirement unmet and needs an out-of-band action that
                    cannot be performed headlessly (e.g. visit a web app to
                    onboard a fresh wallet, fund the account).
    na            — the subsystem this check would inspect is not present /
                    not enforced on this branch, so the check is not
                    applicable. Never blocks readiness.
    unknown       — could not be determined (e.g. a network probe failed).
                    Does not block readiness, but is surfaced so the operator
                    knows the answer is genuinely undetermined.

`ready` is True only when no check is `fail` or `action_needed`. `na` and
`unknown` checks never flip `ready` to False — they are reported but not
treated as blockers.

ZERO FABRICATION: the only onboarding URL emitted here is the real one already
referenced in cli/commands/setup.py (the claim-usdyp error path). It is defined
once below as HL_TESTNET_ONBOARD_URL and reused. No URL is invented.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Real Hyperliquid testnet web app — fresh wallets must connect here once so HL
# "sees" them before deposits / claims work. Single source of truth; identical
# to the URL printed by `hl setup claim-usdyp` in cli/commands/setup.py.
HL_TESTNET_ONBOARD_URL = "https://app.hyperliquid-testnet.xyz"

# Statuses that count as "not ready".
_BLOCKING = {"fail", "action_needed"}


def _check(check_id: str, status: str, detail: str) -> Dict[str, Any]:
    return {"id": check_id, "status": status, "detail": detail}


def _ensure_path() -> None:
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


# ---------------------------------------------------------------------------
# Individual checks. Each returns a single check dict and never raises.
# ---------------------------------------------------------------------------

def check_cli_installed() -> Dict[str, Any]:
    """agent-cli importable in the current interpreter."""
    try:
        import cli  # noqa: F401
        return _check("cli_installed", "pass", "agent-cli package is importable")
    except Exception as e:  # pragma: no cover - import of self basically always works
        return _check("cli_installed", "fail",
                      f"agent-cli not importable: {e}; run 'hl setup bootstrap'")


def check_sdk_installed() -> Dict[str, Any]:
    """hyperliquid-python-sdk present (required to talk to HL)."""
    try:
        import hyperliquid  # noqa: F401
        return _check("hl_sdk_installed", "pass", "hyperliquid-python-sdk installed")
    except ImportError:
        return _check("hl_sdk_installed", "fail",
                      "hyperliquid-python-sdk not installed "
                      "(pip install hyperliquid-python-sdk)")


def check_wallet_configured() -> Dict[str, Any]:
    """A signing key is available: HL_PRIVATE_KEY env or an encrypted keystore."""
    _ensure_path()
    if os.environ.get("HL_PRIVATE_KEY"):
        return _check("wallet_configured", "pass", "HL_PRIVATE_KEY is set")

    try:
        from cli.keystore import list_keystores, _resolve_password
    except Exception as e:  # pragma: no cover
        return _check("wallet_configured", "fail", f"keystore module unavailable: {e}")

    keystores = list_keystores()
    if not keystores:
        return _check("wallet_configured", "fail",
                      "no wallet: set HL_PRIVATE_KEY or run 'hl wallet auto'")

    if _resolve_password():
        return _check("wallet_configured", "pass",
                      f"{len(keystores)} keystore(s) present and password resolvable")
    return _check("wallet_configured", "action_needed",
                  f"{len(keystores)} keystore(s) present but HL_KEYSTORE_PASSWORD "
                  "not set (needed to unlock); set it or check ~/.hl-agent/env")


def _resolve_address() -> Optional[str]:
    """Best-effort wallet address from env key or first keystore. Never raises."""
    _ensure_path()
    priv = os.environ.get("HL_PRIVATE_KEY")
    if priv:
        try:
            from eth_account import Account
            if not priv.startswith("0x"):
                priv = "0x" + priv
            return Account.from_key(priv).address
        except Exception:
            return None
    try:
        from cli.keystore import list_keystores
        keystores = list_keystores()
        if keystores:
            return keystores[0]["address"]
    except Exception:
        return None
    return None


def check_hl_onboarding(testnet: bool = True) -> Dict[str, Any]:
    """Has the wallet been onboarded/funded on Hyperliquid?

    Determined read-only via the HL Info API (clearinghouseState). A non-zero
    account value (or any open position) means HL has seen and funded the
    wallet. If the wallet is unseen / unfunded this cannot be fixed headlessly,
    so we return action_needed plus the REAL onboarding URL. If we cannot reach
    HL we return 'unknown' rather than guessing.
    """
    address = _resolve_address()
    if not address:
        return _check("hl_onboarding", "action_needed",
                      "no wallet to check; configure a wallet first "
                      "(run 'hl wallet auto')")

    net = "testnet" if testnet else "mainnet"
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        info = Info(base_url, skip_ws=True, timeout=10)
        state = info.post("/info", {"type": "clearinghouseState", "user": address})
    except Exception as e:
        return _check("hl_onboarding", "unknown",
                      f"could not query HL {net} for {address}: {e}; "
                      f"if this is a fresh wallet, onboard at {HL_TESTNET_ONBOARD_URL}"
                      if testnet else
                      f"could not query HL {net} for {address}: {e}")

    if not isinstance(state, dict):
        return _check("hl_onboarding", "unknown",
                      f"unexpected HL response for {address} on {net}")

    margin = state.get("marginSummary", {}) or {}
    try:
        account_value = float(margin.get("accountValue", 0) or 0)
    except (TypeError, ValueError):
        account_value = 0.0
    has_positions = bool(state.get("assetPositions"))

    if account_value > 0 or has_positions:
        return _check("hl_onboarding", "pass",
                      f"{address} onboarded on {net} (accountValue=${account_value:g})")

    detail = (
        f"{address} not yet funded/onboarded on {net}. "
        "This needs a one-time browser action and cannot be done headlessly: "
        f"connect the wallet at {HL_TESTNET_ONBOARD_URL}, then deposit/fund."
        if testnet else
        f"{address} not yet funded on {net}; deposit funds to enable trading."
    )
    return _check("hl_onboarding", "action_needed", detail)


def check_usdyp_claim(testnet: bool = True) -> Dict[str, Any]:
    """USDyP testnet claim status (required for YEX markets).

    Reuses the same claim endpoint as `hl setup claim-usdyp`. We query the HL
    spot balances read-only to see if USDyP is already held; if not, we return
    action_needed pointing at the claim command. On mainnet this is na (USDyP
    claim is a testnet faucet concept). Never raises.
    """
    if not testnet:
        return _check("usdyp_claim", "na", "USDyP claim is a testnet faucet; na on mainnet")

    address = _resolve_address()
    if not address:
        return _check("usdyp_claim", "action_needed",
                      "no wallet to check; configure a wallet then run "
                      "'hl setup claim-usdyp'")

    try:
        from hyperliquid.utils import constants
        import requests
        base_url = constants.TESTNET_API_URL
        resp = requests.post(
            f"{base_url}/info",
            json={"type": "spotClearinghouseState", "user": address},
            timeout=10,
        )
        balances = resp.json().get("balances", []) or []
    except Exception as e:
        return _check("usdyp_claim", "unknown",
                      f"could not check USDyP balance for {address}: {e}; "
                      "if unclaimed run 'hl setup claim-usdyp'")

    for b in balances:
        coin = str(b.get("coin", ""))
        try:
            total = float(b.get("total", 0) or 0)
        except (TypeError, ValueError):
            total = 0.0
        if coin.upper().startswith("USDYP") and total > 0:
            return _check("usdyp_claim", "pass",
                          f"USDyP balance present ({total:g}) for {address}")

    return _check("usdyp_claim", "action_needed",
                  f"no USDyP balance for {address}; run 'hl setup claim-usdyp' "
                  "(YEX markets require it)")


def check_builder_code() -> Dict[str, Any]:
    """Builder Code configured (and, where determinable, approved on-chain).

    Configuration state comes from BuilderFeeConfig (builder.py / config.py).
    On-chain approval cannot be read back without a funded client + network
    call, so when configured we report 'pass' for configuration and note that
    approval is verified at order time / via `hl builder approve`.
    """
    _ensure_path()
    try:
        from cli.config import TradingConfig
        bcfg = TradingConfig().get_builder_config()
    except Exception as e:  # pragma: no cover
        return _check("builder_code", "unknown", f"could not load builder config: {e}")

    if not bcfg.enabled:
        return _check("builder_code", "fail",
                      "builder fee not configured; set BUILDER_ADDRESS and "
                      "BUILDER_FEE_TENTHS_BPS")
    return _check("builder_code", "pass",
                  f"builder configured: {bcfg.fee_bps} bps -> {bcfg.builder_address}; "
                  "run 'hl builder approve' once per account to approve on-chain")


def check_ai_provider_key() -> Dict[str, Any]:
    """AI provider key present in env (needed for LLM-driven strategies)."""
    candidates = (
        "ANTHROPIC_API_KEY",
        "AI_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    )
    found = [name for name in candidates if os.environ.get(name)]
    if found:
        return _check("ai_provider_key", "pass", f"AI provider key set: {found[0]}")
    return _check("ai_provider_key", "fail",
                  "no AI provider key in env (set one of "
                  + ", ".join(candidates) + "); only needed for LLM strategies")


def check_fleet_health() -> Dict[str, Any]:
    """Fleet subsystem health.

    There is no fleet orchestration subsystem on this branch, so this is na.
    Returns gracefully — never crashes — so the report stays stable as the
    feature lands later.
    """
    _ensure_path()
    try:
        import importlib.util
        if importlib.util.find_spec("cli.fleet") is not None:  # pragma: no cover
            return _check("fleet_health", "unknown",
                          "fleet subsystem present but health probe not wired")
    except Exception:
        pass
    return _check("fleet_health", "na",
                  "no fleet subsystem on this branch")


def check_oracle_freshness() -> Dict[str, Any]:
    """Oracle freshness enforcement.

    Oracle freshness is not enforced on this branch, so this is na. Returns
    gracefully — never crashes.
    """
    return _check("oracle_freshness", "na",
                  "oracle freshness not enforced on this branch")


# Ordered registry of checks. Each entry is (callable, needs_testnet_flag).
_CHECKS: List[tuple[Callable[..., Dict[str, Any]], bool]] = [
    (check_cli_installed, False),
    (check_sdk_installed, False),
    (check_wallet_configured, False),
    (check_hl_onboarding, True),
    (check_usdyp_claim, True),
    (check_builder_code, False),
    (check_ai_provider_key, False),
    (check_fleet_health, False),
    (check_oracle_freshness, False),
]


def _resolve_testnet() -> bool:
    return os.environ.get("HL_TESTNET", "true").lower() == "true"


def build_readiness_report(
    workspace: Optional[str] = None,
    testnet: Optional[bool] = None,
    probe_network: bool = True,
) -> Dict[str, Any]:
    """Aggregate every check into a single readiness report.

    Args:
        workspace: opaque workspace id, echoed back into the report. The CLI is
            the backend truth for a given institution's workspace; the id is not
            interpreted here, only surfaced.
        testnet: override network; defaults to HL_TESTNET env (true).
        probe_network: when False, network-dependent checks (HL onboarding,
            USDyP) are reported as 'unknown' without making any HTTP call. Lets
            tests and air-gapped callers get a deterministic shape.

    Returns:
        {"workspace", "network", "ready", "summary", "checks": [...]}
    """
    if testnet is None:
        testnet = _resolve_testnet()

    checks: List[Dict[str, Any]] = []
    for fn, needs_testnet in _CHECKS:
        try:
            if not probe_network and fn in (check_hl_onboarding, check_usdyp_claim):
                # Skip the live probe but keep the check present and honest.
                checks.append(_check(
                    _stable_id(fn),
                    "unknown",
                    "network probe skipped (probe_network=False)",
                ))
                continue
            result = fn(testnet=testnet) if needs_testnet else fn()
        except Exception as e:  # defensive: a check must never break the report
            result = _check(_stable_id(fn), "unknown", f"check raised: {e}")
        checks.append(result)

    blocking = [c for c in checks if c["status"] in _BLOCKING]
    ready = len(blocking) == 0

    counts: Dict[str, int] = {}
    for c in checks:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    return {
        "workspace": workspace,
        "network": "testnet" if testnet else "mainnet",
        "ready": ready,
        "summary": {
            "total": len(checks),
            "blocking": len(blocking),
            "blocking_ids": [c["id"] for c in blocking],
            "counts": counts,
        },
        "checks": checks,
    }


# Map a check callable to its stable id without invoking it (used on the
# probe-skip and exception paths so ids stay consistent).
_STABLE_IDS: Dict[Callable[..., Dict[str, Any]], str] = {
    check_cli_installed: "cli_installed",
    check_sdk_installed: "hl_sdk_installed",
    check_wallet_configured: "wallet_configured",
    check_hl_onboarding: "hl_onboarding",
    check_usdyp_claim: "usdyp_claim",
    check_builder_code: "builder_code",
    check_ai_provider_key: "ai_provider_key",
    check_fleet_health: "fleet_health",
    check_oracle_freshness: "oracle_freshness",
}


def _stable_id(fn: Callable[..., Dict[str, Any]]) -> str:
    return _STABLE_IDS.get(fn, getattr(fn, "__name__", "unknown"))
