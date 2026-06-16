"""Local session-policy enforcement — a self-contained guard for mutating CLI actions.

WHAT THIS IS
------------
A *local* allow-list / limit guard that gates the CLI's mutating operations
(run / trade / builder-approve, and future fleet/house/hedge/margin/strategy
commands). It answers one question before an action runs: "is this action
permitted by the active session policy?" — and refuses with a clear reason if
not.

WHAT THIS IS NOT
----------------
This module performs **NO network calls** and has **NO web-auth client code**.
It does not talk to a dashboard, an IDE, an auth server, or any HTTP endpoint.
It only *accepts* a policy payload that some future IDE / web-auth / dashboard
flow could produce out-of-band (e.g. a signed session that the operator drops
on disk or into an env var). The contract is the JSON schema documented below;
how that JSON is produced is explicitly out of scope here.

If no policy is supplied, the guard is a no-op and the CLI behaves exactly as
it does today (fully permissive).

JSON PAYLOAD SCHEMA (the web-auth / IDE contract)
-------------------------------------------------
A policy is a single JSON object. Every field is optional; an absent/null field
means "no constraint on this dimension". Example::

    {
      "wallets": ["0xAbc...123", "0xDef...456"],
      "network": "testnet",
      "allowed_actions": ["run", "trade", "builder-approve"],
      "allowed_strategies": ["avellaneda_mm", "engine_mm"],
      "allowed_markets": ["ETH-PERP", "BTC-PERP"],
      "max_notional_usd_per_action": 5000.0,
      "daily_notional_limit_usd": 25000.0
    }

Field semantics:

* ``wallets``       — list[str]. Allowed signer addresses. Empty/absent = any
                      wallet. Compared case-insensitively (EVM addresses).
* ``network``       — "mainnet" | "testnet" | null. Absent/null = any network.
* ``allowed_actions``    — list[str]. Allowed action names (see ACTIONS below).
                           Empty/absent = all actions allowed.
* ``allowed_strategies`` — list[str]. Allowed strategy names for ``run``.
                           Empty/absent = any strategy.
* ``allowed_markets``    — list[str]. Allowed instruments/markets for ``trade``.
                           Empty/absent = any market.
* ``max_notional_usd_per_action`` — float | null. Per-action notional ceiling
                           in USD. null = no per-action ceiling.
* ``daily_notional_limit_usd``    — float | null. Cumulative per-(wallet,
                           network, workspace) per-UTC-day notional ceiling in
                           USD. null = no daily ceiling.

Unknown keys are ignored so the contract can grow without breaking older CLIs.

ACTION NAMES (canonical)
------------------------
``run``             — start an autonomous trading loop (cli/commands/run.py)
``trade``           — place a single manual order (cli/commands/trade.py)
``builder-approve`` — approve a builder fee on-chain (cli/commands/builder.py)

Future commands should reuse these or add their own canonical name and pass it
to ``guard_or_exit`` / ``enforce`` (e.g. ``fleet``, ``house``, ``hedge``,
``margin``, ``strategy``).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("session_policy")

# Env var that carries the policy: either a path to a JSON file, or inline JSON.
POLICY_ENV_VAR = "NUNCHI_SESSION_POLICY"

# Local store for daily notional counters. Self-contained — no dependency on
# StateDB / JSONLStore so this guard works even in isolation.
DEFAULT_COUNTERS_PATH = "data/cli/policy-counters.json"

# Canonical action names (kept as module constants so callers don't typo them).
ACTION_RUN = "run"
ACTION_TRADE = "trade"
ACTION_BUILDER_APPROVE = "builder-approve"


class PolicyViolation(Exception):
    """Raised when a requested action is refused by the active session policy."""


def _norm_addr(addr: Optional[str]) -> Optional[str]:
    """Normalise an EVM address for comparison (lowercase, stripped)."""
    if addr is None:
        return None
    return addr.strip().lower()


@dataclass
class SessionPolicy:
    """A local, self-contained set of constraints on mutating CLI actions.

    Pure data + ``enforce``. No I/O, no network. Construct via ``from_dict`` /
    ``from_json`` or load from disk/env via :func:`load_policy`.
    """

    wallets: List[str] = field(default_factory=list)         # allowed signers; empty = any
    network: Optional[str] = None                            # "mainnet" | "testnet" | None = any
    allowed_actions: List[str] = field(default_factory=list)     # empty = all
    allowed_strategies: List[str] = field(default_factory=list)  # empty = any
    allowed_markets: List[str] = field(default_factory=list)     # empty = any
    max_notional_usd_per_action: Optional[float] = None      # None = no per-action cap
    daily_notional_limit_usd: Optional[float] = None         # None = no daily cap

    # ---- serialisation ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wallets": list(self.wallets),
            "network": self.network,
            "allowed_actions": list(self.allowed_actions),
            "allowed_strategies": list(self.allowed_strategies),
            "allowed_markets": list(self.allowed_markets),
            "max_notional_usd_per_action": self.max_notional_usd_per_action,
            "daily_notional_limit_usd": self.daily_notional_limit_usd,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionPolicy":
        if not isinstance(data, dict):
            raise ValueError(f"session policy must be a JSON object, got {type(data).__name__}")

        network = data.get("network")
        if network is not None:
            network = str(network).strip().lower()
            if network not in ("mainnet", "testnet"):
                raise ValueError(
                    f"session policy 'network' must be 'mainnet', 'testnet', or null; got {network!r}"
                )

        def _str_list(key: str) -> List[str]:
            val = data.get(key) or []
            if isinstance(val, str):
                raise ValueError(f"session policy '{key}' must be a list, not a string")
            return [str(x) for x in val]

        def _opt_float(key: str) -> Optional[float]:
            val = data.get(key)
            if val is None:
                return None
            try:
                f = float(val)
            except (TypeError, ValueError):
                raise ValueError(f"session policy '{key}' must be a number or null; got {val!r}")
            if f < 0:
                raise ValueError(f"session policy '{key}' must be >= 0; got {f}")
            return f

        return cls(
            wallets=_str_list("wallets"),
            network=network,
            allowed_actions=_str_list("allowed_actions"),
            allowed_strategies=_str_list("allowed_strategies"),
            allowed_markets=_str_list("allowed_markets"),
            max_notional_usd_per_action=_opt_float("max_notional_usd_per_action"),
            daily_notional_limit_usd=_opt_float("daily_notional_limit_usd"),
        )

    @classmethod
    def from_json(cls, text: str) -> "SessionPolicy":
        return cls.from_dict(json.loads(text))

    # ---- enforcement ------------------------------------------------------

    def enforce(
        self,
        action: str,
        *,
        wallet: Optional[str] = None,
        network: Optional[str] = None,
        strategy: Optional[str] = None,
        market: Optional[str] = None,
        notional_usd: Optional[float] = None,
    ) -> None:
        """Raise :class:`PolicyViolation` if ``action`` violates this policy.

        Only checks constraints that are *set* on the policy AND for which the
        caller supplied a value. A ``None`` context value means "not applicable
        / unknown" and is not checked (callers pass what they know). The single
        exception is ``daily_notional_limit_usd``, which is enforced separately
        via :class:`PolicyCounters` since it requires persistent state.
        """
        # action allow-list
        if self.allowed_actions and action not in self.allowed_actions:
            raise PolicyViolation(
                f"action '{action}' is not in the allowed actions "
                f"({', '.join(self.allowed_actions)})"
            )

        # wallet allow-list
        if self.wallets and wallet is not None:
            allowed = {_norm_addr(w) for w in self.wallets}
            if _norm_addr(wallet) not in allowed:
                raise PolicyViolation(
                    f"wallet {wallet} is not in the allowed signer list "
                    f"({', '.join(self.wallets)})"
                )

        # network pin
        if self.network is not None and network is not None:
            if str(network).strip().lower() != self.network:
                raise PolicyViolation(
                    f"network '{network}' is not permitted; policy pins network to '{self.network}'"
                )

        # strategy allow-list
        if self.allowed_strategies and strategy is not None:
            if strategy not in self.allowed_strategies:
                raise PolicyViolation(
                    f"strategy '{strategy}' is not in the allowed strategies "
                    f"({', '.join(self.allowed_strategies)})"
                )

        # market allow-list
        if self.allowed_markets and market is not None:
            if market not in self.allowed_markets:
                raise PolicyViolation(
                    f"market '{market}' is not in the allowed markets "
                    f"({', '.join(self.allowed_markets)})"
                )

        # per-action notional ceiling
        if self.max_notional_usd_per_action is not None and notional_usd is not None:
            if notional_usd > self.max_notional_usd_per_action:
                raise PolicyViolation(
                    f"order notional ${notional_usd:,.2f} exceeds the per-action limit "
                    f"of ${self.max_notional_usd_per_action:,.2f}"
                )


# ---------------------------------------------------------------------------
# Policy source (local only): explicit path > env var. No web-auth.
# ---------------------------------------------------------------------------

def _load_policy_text(value: str) -> Dict[str, Any]:
    """Resolve a policy *value* (a file path OR inline JSON) to a dict."""
    stripped = value.strip()
    # Inline JSON object?
    if stripped.startswith("{"):
        return json.loads(stripped)
    # Otherwise treat it as a file path.
    p = Path(stripped).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"session policy file not found: {p}")
    return json.loads(p.read_text())


def load_policy(explicit_path: Optional[str] = None) -> Optional[SessionPolicy]:
    """Load the active session policy from a local source, or return None.

    Resolution order (no network, no web-auth):

    1. ``explicit_path`` — a ``--policy`` CLI value (file path OR inline JSON).
    2. ``NUNCHI_SESSION_POLICY`` env var (file path OR inline JSON).
    3. Neither set → ``None`` (fully permissive — behaves exactly as today).
    """
    source: Optional[str] = None
    if explicit_path:
        source = explicit_path
    else:
        env_val = os.environ.get(POLICY_ENV_VAR)
        if env_val and env_val.strip():
            source = env_val

    if source is None:
        return None

    data = _load_policy_text(source)
    return SessionPolicy.from_dict(data)


def load_policy_or_exit(explicit_path: Optional[str] = None) -> Optional[SessionPolicy]:
    """Load policy for CLI callers and render parse errors consistently."""
    import typer

    try:
        return load_policy(explicit_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        typer.echo(f"Session policy error: {e}", err=True)
        raise typer.Exit(2)


# ---------------------------------------------------------------------------
# Daily notional counters — local, self-contained JSON store.
# Keyed by (wallet, network, workspace) with UTC-midnight rollover.
# ---------------------------------------------------------------------------

def _utc_day(now: Optional[datetime] = None) -> str:
    """Return the current UTC day as 'YYYY-MM-DD'. ``now`` overridable for tests."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _counter_key(wallet: Optional[str], network: Optional[str], workspace: str) -> str:
    """Build the composite counter key. None segments become '*'."""
    w = _norm_addr(wallet) or "*"
    n = (network or "*").strip().lower()
    ws = workspace or "*"
    return f"{w}|{n}|{ws}"


@dataclass
class PolicyCounters:
    """Local per-(wallet, network, workspace) daily notional counters.

    Persisted as a small JSON file (default ``data/cli/policy-counters.json``).
    Self-contained: does not depend on StateDB/JSONLStore or any other module.
    Rolls over at UTC midnight — a stored day != current UTC day resets to 0.
    """

    path: str = DEFAULT_COUNTERS_PATH

    def _read(self) -> Dict[str, Any]:
        p = Path(self.path)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt or unreadable policy counters at %s; treating as empty", p)
            return {}

    def _write(self, data: Dict[str, Any]) -> None:
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(p)  # atomic

    def used_today(
        self,
        wallet: Optional[str],
        network: Optional[str],
        workspace: str,
        *,
        now: Optional[datetime] = None,
    ) -> float:
        """Return USD notional already used today for this key (0 after rollover)."""
        data = self._read()
        entry = data.get(_counter_key(wallet, network, workspace))
        if not entry:
            return 0.0
        if entry.get("day") != _utc_day(now):
            return 0.0  # stale day → rolled over
        try:
            return float(entry.get("notional_usd", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def record(
        self,
        wallet: Optional[str],
        network: Optional[str],
        workspace: str,
        notional_usd: float,
        *,
        now: Optional[datetime] = None,
    ) -> float:
        """Add ``notional_usd`` to today's tally for this key and return the new total.

        Handles UTC rollover: if the stored entry is from a previous day it is
        reset before adding.
        """
        day = _utc_day(now)
        data = self._read()
        key = _counter_key(wallet, network, workspace)
        entry = data.get(key)
        if not entry or entry.get("day") != day:
            current = 0.0
        else:
            try:
                current = float(entry.get("notional_usd", 0.0))
            except (TypeError, ValueError):
                current = 0.0
        new_total = current + max(0.0, float(notional_usd))
        data[key] = {"day": day, "notional_usd": new_total}
        self._write(data)
        return new_total

    def check_daily(
        self,
        policy: SessionPolicy,
        wallet: Optional[str],
        network: Optional[str],
        workspace: str,
        notional_usd: float,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Raise :class:`PolicyViolation` if recording ``notional_usd`` would breach
        the policy's ``daily_notional_limit_usd``. Does NOT record — call
        :meth:`record` after the action is permitted/executed.
        """
        if policy.daily_notional_limit_usd is None or notional_usd is None:
            return
        used = self.used_today(wallet, network, workspace, now=now)
        if used + notional_usd > policy.daily_notional_limit_usd:
            remaining = max(0.0, policy.daily_notional_limit_usd - used)
            raise PolicyViolation(
                f"daily notional limit reached: ${used:,.2f} already used today, "
                f"this order is ${notional_usd:,.2f}, limit is "
                f"${policy.daily_notional_limit_usd:,.2f} "
                f"(${remaining:,.2f} remaining)"
            )


# ---------------------------------------------------------------------------
# Workspace identity (local) — segments the daily counters by checkout/cwd so
# two independent working copies don't share a tally. No network/identity call.
# ---------------------------------------------------------------------------

def current_workspace() -> str:
    """Return a stable local workspace id for counter keying.

    Order: ``NUNCHI_WORKSPACE`` env override > current working directory. This
    is purely local — it never resolves a remote identity.
    """
    ws = os.environ.get("NUNCHI_WORKSPACE")
    if ws and ws.strip():
        return ws.strip()
    return os.getcwd()


# ---------------------------------------------------------------------------
# Shared CLI guard — the single reusable entry point mutating commands call.
# ---------------------------------------------------------------------------

def guard_or_exit(
    action: str,
    *,
    policy_path: Optional[str] = None,
    wallet: Optional[str] = None,
    network: Optional[str] = None,
    strategy: Optional[str] = None,
    market: Optional[str] = None,
    notional_usd: Optional[float] = None,
    counters_path: str = DEFAULT_COUNTERS_PATH,
    record: bool = False,
    workspace: Optional[str] = None,
) -> Optional[SessionPolicy]:
    """Load the active policy, enforce it for ``action``, and exit cleanly on violation.

    This is the shared helper every mutating command should call *before*
    performing its side effect. Behaviour:

    * No policy configured (no ``--policy`` / no env) → returns ``None`` and the
      command proceeds exactly as today (fully permissive).
    * Policy configured and satisfied → returns the :class:`SessionPolicy`
      (so the caller can, e.g., record notional afterwards).
    * Policy configured and violated → prints a clear error to stderr and
      raises ``typer.Exit(2)``.

    Daily-notional handling: when a ``daily_notional_limit_usd`` is set and a
    ``notional_usd`` is supplied, the *pending* order is checked against the
    remaining daily budget here. Pass ``record=True`` to also persist the
    notional to the local counter (do this only once the action is actually
    going to execute, to avoid double counting).

    Reusable by not-yet-merged commands (fleet/house/hedge/margin/strategy):
    they only need to pass their canonical ``action`` name plus whatever
    context they know (wallet/network/market/notional).
    """
    import typer  # local import keeps pure-data import path typer-free

    policy = load_policy_or_exit(policy_path)

    if policy is None:
        return None  # permissive default — unchanged behaviour

    ws = workspace or current_workspace()
    counters = PolicyCounters(counters_path)

    try:
        policy.enforce(
            action,
            wallet=wallet,
            network=network,
            strategy=strategy,
            market=market,
            notional_usd=notional_usd,
        )
        if notional_usd is not None:
            counters.check_daily(policy, wallet, network, ws, notional_usd)
    except PolicyViolation as e:
        typer.echo(f"REFUSED by session policy: {e}", err=True)
        raise typer.Exit(2)

    if record and notional_usd is not None:
        counters.record(wallet, network, ws, notional_usd)

    return policy
