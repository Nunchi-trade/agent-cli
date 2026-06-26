"""hl hedge — BTCSWP CFI v2 funding-rate hedge.

Feature surface:

    hl hedge propose [COIN]                  show proposal, no execute
    hl hedge execute [COIN] [--dry-run]      preview yex:{COIN}SWP order without signing
    hl hedge status [--coin C] [--watch]     active hedges + live drift/savings
    hl hedge backtest --coin BTC [--days N]  wrap hedge_calculator.py
    hl hedge auto [--coins ...] [--dry-run]  agent-controlled auto-open loop

All math + oracle access is delegated to `strategies/cfi_hedge.py` and
`quoting_engine/feeds/seda_oracle.py`. Signing + submission are delegated to
the Hyperliquid Python SDK via `DirectHLProxy.place_order()` — same code path
as `hl trade`. No custom signing here.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer

hedge_app = typer.Typer(
    name="hedge",
    help="BTCSWP CFI v2 funding-rate hedge — propose, execute, status, backtest.",
    no_args_is_help=True,
)


def _boot_cli():
    """Same project-root + logging setup that trade_cmd / account_cmd use."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def _hedges_path() -> Path:
    """`~/.nunchi/hedges.json` — matches existing conventions."""
    p = Path.home() / ".nunchi"
    p.mkdir(parents=True, exist_ok=True)
    return p / "hedges.json"


def _load_hedges() -> list:
    path = _hedges_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_hedges(hedges: list) -> None:
    path = _hedges_path()
    path.write_text(json.dumps(hedges, indent=2))


def _hl_position_for_coin(state: dict, coin: str) -> Optional[dict]:
    """Find the first open position matching `coin` in the merged account state."""
    coin_upper = coin.upper()
    for entry in state.get("positions", []):
        pos = entry.get("position", {})
        if pos.get("coin", "").upper() == coin_upper:
            return pos
    return None


def _position_to_summary(raw: dict, coin_override: Optional[str] = None):
    """Convert an HL `assetPositions[].position` dict to `HLPositionSummary`."""
    from strategies.cfi_hedge import HLPositionSummary

    coin = (coin_override or raw.get("coin", "")).upper()
    szi = float(raw.get("szi", "0"))
    size_coin = abs(szi)
    entry_px = float(raw.get("entryPx", "0") or 0)
    notional = abs(float(raw.get("positionValue", "0") or (size_coin * entry_px)))
    mark_px = (notional / size_coin) if size_coin else entry_px
    leverage_info = raw.get("leverage") or {}
    leverage = int(leverage_info.get("value", 1) or 1)
    cum = raw.get("cumFunding") or {}
    cum_funding = float(cum.get("allTime") or cum.get("sinceOpen") or 0)
    return HLPositionSummary(
        coin=coin,
        side="long" if szi >= 0 else "short",
        size_coin=size_coin,
        entry_px=entry_px,
        mark_px=mark_px,
        notional_usd=notional,
        leverage=leverage,
        unrealized_pnl_usd=float(raw.get("unrealizedPnl", "0") or 0),
        cum_funding_usd=cum_funding,
    )


def _build_proposal(hl, coin: str):
    """Build a hedge proposal for the user's current `coin` perp position.

    Returns (proposal, snapshot) or raises typer.Exit if no position open.
    """
    from strategies.cfi_hedge import build_cfi_hedge_proposal, get_cfi_profile
    from quoting_engine.feeds.seda_oracle import (
        fetch_btcswp_snapshot,
        fetch_hl_current_funding_hr,
    )

    profile = get_cfi_profile(coin)
    if profile is None:
        typer.echo(f"Error: no deployed CFI v2 profile for coin '{coin}'", err=True)
        raise typer.Exit(2)

    state = hl.get_account_state()
    if not state:
        typer.echo("Error: could not fetch HL account state", err=True)
        raise typer.Exit(1)

    raw_pos = _hl_position_for_coin(state, coin)
    if raw_pos is None:
        typer.echo(
            f"No open {coin} perp position on Hyperliquid for "
            f"{state.get('address', '?')}. Open one on app.hyperliquid.xyz and retry.",
            err=True,
        )
        raise typer.Exit(1)

    position = _position_to_summary(raw_pos, coin_override=coin)
    snapshot = fetch_btcswp_snapshot(profile)
    current_funding = fetch_hl_current_funding_hr(coin)
    if current_funding is None:
        # Fall back to oracle's r_ema if HL didn't answer.
        current_funding = snapshot.r_ema_hr

    proposal = build_cfi_hedge_proposal(
        user_address=state.get("address", "demo"),
        position=position,
        current_funding_hr=current_funding,
        k_fixed_hr=snapshot.k_fixed_hr,
    )
    return proposal, snapshot


# ─── propose ─────────────────────────────────────────────────────────────────


@hedge_app.command("propose")
def propose_cmd(
    coin: str = typer.Argument("BTC", help="Coin to hedge (BTC, ETH)"),
    asset: Optional[str] = typer.Option(None, "--asset", help="Alias for coin in pure sizing mode."),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
    side: str = typer.Option("long", "--side", help="Perp exposure side for pure sizing: long or short"),
    perp_notional: Optional[float] = typer.Option(
        None,
        "--perp-notional",
        help="Pure sizing mode: absolute perp notional in USD; does not fetch account state.",
    ),
    funding_apr: Optional[float] = typer.Option(
        None,
        "--funding-apr",
        help="Pure sizing mode: annualized funding APR. Accepts 0.42 or 42 for 42%.",
    ),
    funding_rate_8h: Optional[float] = typer.Option(
        None,
        "--funding-rate-8h",
        help="Pure sizing mode: 8h funding rate as a decimal, e.g. 0.0003.",
    ),
    vol_multiplier: float = typer.Option(15.0, "--vol-multiplier", help="BTCSWP hedge multiplier."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON in pure sizing mode."),
):
    """Show a CFI v2 hedge proposal without executing.

    By default this reads the current account position. Passing
    `--perp-notional` switches to pure sizing mode for agents/docs/tests.
    """
    if perp_notional is not None:
        from modules.funding_hedge import format_proposal, propose_funding_hedge

        try:
            proposal = propose_funding_hedge(
                asset=asset or coin,
                perp_side=side,
                perp_notional_usd=perp_notional,
                funding_apr=funding_apr,
                funding_rate_8h=funding_rate_8h,
                vol_multiplier=vol_multiplier,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

        if json_output:
            typer.echo(json.dumps(proposal.to_dict(), indent=2))
        else:
            typer.echo(format_proposal(proposal))
        return

    _boot_cli()

    from cli.config import TradingConfig
    from cli.hedge_display import hedge_proposal_block
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    cfg = TradingConfig()
    private_key = cfg.get_private_key()
    raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
    hl = DirectHLProxy(raw_hl)

    proposal, snapshot = _build_proposal(hl, coin)
    typer.echo(hedge_proposal_block(proposal, snapshot, mainnet=mainnet))


# ─── execute ─────────────────────────────────────────────────────────────────


@hedge_app.command("execute")
def execute_cmd(
    coin: str = typer.Argument("BTC", help="Coin to hedge (BTC, ETH)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only; do not sign or submit"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirm"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """Build the proposal and optionally sign + submit a real yex:{COIN}SWP order.

    Persists the resulting HedgeJob to `~/.nunchi/hedges.json`.
    """
    _boot_cli()

    from cli.config import TradingConfig
    from cli.display import BOLD, GREEN, RESET
    from cli.hedge_display import hedge_proposal_block
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    cfg = TradingConfig()
    private_key = cfg.get_private_key()
    raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
    hl = DirectHLProxy(raw_hl)

    proposal, snapshot = _build_proposal(hl, coin)
    typer.echo(hedge_proposal_block(proposal, snapshot, mainnet=mainnet))

    # Size the order in CFI v2 (BTCSWP) units. SDK rounds to szDecimals.
    wire_px = snapshot.oracle_px or proposal.profile.baseline_b0
    size = proposal.hedge_notional_usd / wire_px
    # Slippage so the IOC fills. Buy → up, Sell → down.
    is_buy = proposal.legs[1].side == "long"
    slippage = 1.002 if is_buy else 0.998
    price = wire_px * slippage

    typer.echo(
        f"\nPlacing {('BUY' if is_buy else 'SELL')} {size:.6f} "
        f"{proposal.profile.cfi_instrument} @ ${price:,.4f} (Ioc)"
    )

    if dry_run:
        typer.echo("DRY-RUN: no order submitted and no hedge state persisted.")
        return

    if not yes:
        if not typer.confirm("Sign + submit this hedge?"):
            typer.echo("Aborted.")
            raise typer.Exit(0)

    fill = hl.place_order(
        instrument=proposal.profile.cfi_instrument,
        side="buy" if is_buy else "sell",
        size=size,
        price=price,
        tif="Ioc",
    )

    if fill is None:
        typer.echo(
            f"{BOLD}No fill{RESET} — order may have been rejected or not matched. "
            f"Nothing persisted.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(
        f"{GREEN}{BOLD}Filled:{RESET} {fill.side.upper()} {fill.quantity} "
        f"{fill.instrument} @ {fill.price} (oid={fill.oid})"
    )

    # Persist the HedgeJob.
    job_id = f"HEDGE-{int(time.time() * 1000)}"
    job = {
        "id": job_id,
        "user_address": proposal.user_address,
        "coin": proposal.source_position.coin,
        "instrument": proposal.profile.cfi_instrument,
        "perp_notional_usd": proposal.source_position.notional_usd,
        "hedge_notional_usd": proposal.hedge_notional_usd,
        "locked_k2_apy": proposal.k_fixed_apy,
        "current_funding_apy": proposal.current_funding_apy,
        "oid": str(fill.oid),
        "fill_price": fill.price,
        "fill_quantity": fill.quantity,
        "submitted_at_ms": int(time.time() * 1000),
        "status": "active",
        "cumulative_savings_usd": 0.0,
        "last_sample_at_ms": int(time.time() * 1000),
        "network": "mainnet" if mainnet else "testnet",
    }
    hedges = _load_hedges()
    hedges.insert(0, job)
    _save_hedges(hedges)
    typer.echo(f"Persisted {GREEN}{job_id}{RESET} → {_hedges_path()}")


# ─── status ──────────────────────────────────────────────────────────────────


@hedge_app.command("status")
def status_cmd(
    coin: Optional[str] = typer.Option(None, "--coin", help="Filter to one coin"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Refresh every 5s until Ctrl-C"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """Show active hedges + live drift vs locked K2 + cumulative savings."""
    _boot_cli()

    from strategies.cfi_hedge import (
        accumulate_savings,
        get_cfi_profile,
    )
    from cli.hedge_display import hedge_status_block
    from quoting_engine.feeds.seda_oracle import (
        fetch_btcswp_snapshot,
        fetch_hl_current_funding_hr,
    )

    def _refresh():
        hedges = _load_hedges()
        if coin:
            hedges = [h for h in hedges if h.get("coin", "").upper() == coin.upper()]
        live = []
        now_ms = int(time.time() * 1000)
        for h in hedges:
            if h.get("status") != "active":
                live.append({"job": h, "snapshot": None, "drift_apy": 0.0, "savings_usd": h.get("cumulative_savings_usd", 0.0)})
                continue
            profile = get_cfi_profile(h.get("coin", "BTC"))
            if profile is None:
                live.append({"job": h, "snapshot": None, "drift_apy": 0.0, "savings_usd": h.get("cumulative_savings_usd", 0.0)})
                continue
            try:
                snap = fetch_btcswp_snapshot(profile)
            except Exception:
                snap = None
            current_hr = fetch_hl_current_funding_hr(h.get("coin", "BTC"))
            if current_hr is None and snap is not None:
                current_hr = snap.r_ema_hr
            if current_hr is None:
                live.append({"job": h, "snapshot": snap, "drift_apy": 0.0, "savings_usd": h.get("cumulative_savings_usd", 0.0)})
                continue

            current_annual = current_hr * 8760
            locked_annual = h.get("locked_k2_apy", 0.0)
            dt_ms = max(0, now_ms - int(h.get("last_sample_at_ms", now_ms)))
            new_savings = accumulate_savings(
                prior_savings_usd=float(h.get("cumulative_savings_usd", 0.0)),
                current_annual=current_annual,
                locked_annual=locked_annual,
                notional_usd=float(h.get("perp_notional_usd", 0.0)),
                dt_ms=dt_ms,
            )
            # Persist the running counter so subsequent invocations resume cleanly.
            h["cumulative_savings_usd"] = new_savings
            h["last_sample_at_ms"] = now_ms
            live.append({
                "job": h,
                "snapshot": snap,
                "drift_apy": current_annual - locked_annual,
                "savings_usd": new_savings,
                "current_funding_apy": current_annual,
            })
        # Write back updated counters.
        full = _load_hedges()
        for entry in live:
            for i, fj in enumerate(full):
                if fj.get("id") == entry["job"].get("id"):
                    full[i] = entry["job"]
                    break
        _save_hedges(full)
        return live

    if not watch:
        live = _refresh()
        if not live:
            typer.echo("No hedges recorded. Run `hl hedge execute` to open one.")
            raise typer.Exit(0)
        typer.echo(hedge_status_block(live, mainnet=mainnet))
        return

    typer.echo("Polling every 5s — Ctrl-C to exit.\n")
    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            live = _refresh()
            if not live:
                typer.echo("No hedges recorded.")
            else:
                typer.echo(hedge_status_block(live, mainnet=mainnet))
            time.sleep(5)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


# ─── backtest ────────────────────────────────────────────────────────────────


@hedge_app.command("backtest")
def backtest_cmd(
    coin: str = typer.Option("BTC", "--coin", help="Coin (BTC or ETH)"),
    asset: Optional[str] = typer.Option(None, "--asset", help="Alias for --coin in --csv mode."),
    days: int = typer.Option(365, "--days", help="Backtest window"),
    notional: float = typer.Option(1_000_000, "--notional", "-n"),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Pure local cashflow mode: CSV with funding_rate_8h/funding_rate and optional hedge_rate_8h.",
    ),
    side: str = typer.Option("long", "--side", help="Perp exposure side for --csv mode: long or short"),
    perp_notional: Optional[float] = typer.Option(
        None,
        "--perp-notional",
        help="Pure --csv mode: absolute perp notional in USD; overrides --notional.",
    ),
    vol_multiplier: float = typer.Option(15.0, "--vol-multiplier", help="BTCSWP hedge multiplier for --csv mode."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON in --csv mode."),
    script: Optional[Path] = typer.Option(
        None,
        "--script",
        help="Override path to hedge_calculator.py (default: ~/hyperliquid-funding-rate-perps/tools/hedge_calculator.py)",
    ),
):
    """Run a CFI v2 hedge backtest via the reference Python tool.

    Shells out to `~/hyperliquid-funding-rate-perps/tools/hedge_calculator.py
    --backtest --asset {COIN} --notional {N}`. Output is streamed through.
    Passing `--csv` switches to pure local cashflow mode.
    """
    if csv_path is not None:
        from modules.funding_hedge import backtest_funding_hedge_csv, format_backtest

        try:
            backtest = backtest_funding_hedge_csv(
                csv_path=csv_path,
                asset=asset or coin,
                perp_side=side,
                perp_notional_usd=perp_notional if perp_notional is not None else notional,
                vol_multiplier=vol_multiplier,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

        if json_output:
            typer.echo(json.dumps(backtest.to_dict(), indent=2))
        else:
            typer.echo(format_backtest(backtest))
        return

    _boot_cli()

    script_path = script or (
        Path.home()
        / "hyperliquid-funding-rate-perps"
        / "tools"
        / "hedge_calculator.py"
    )
    if not script_path.exists():
        typer.echo(
            f"Error: hedge_calculator.py not found at {script_path}. "
            f"Pass --script to override.",
            err=True,
        )
        raise typer.Exit(2)

    cmd = [
        sys.executable,
        str(script_path),
        "--backtest",
        "--asset",
        coin.upper(),
        "--notional",
        str(notional),
    ]
    # `days` is implicit in the canned data the script consumes (365d JSON).
    _ = days
    typer.echo(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    raise typer.Exit(result.returncode)


# ─── auto (agent-controlled) ─────────────────────────────────────────────────


@hedge_app.command("auto")
def auto_cmd(
    coins: str = typer.Option(
        "BTC", "--coins",
        help="Comma-separated coins to watch (e.g. 'BTC' or 'BTC,ETH'). "
             "Only coins with a deployed CFI v2 profile are eligible.",
    ),
    notional_trigger: float = typer.Option(
        100_000.0, "--notional-trigger",
        help="Open a hedge when unhedged perp notional exceeds this (USD).",
    ),
    max_hedge_notional: float = typer.Option(
        50_000.0, "--max-hedge-notional",
        help="Per-action ceiling on the CFI v2 hedge notional (USD). "
             "Caps the 1/L sizing.",
    ),
    max_per_day: int = typer.Option(
        5, "--max-per-day",
        help="Hard ceiling on hedge opens per UTC day.",
    ),
    min_interval: int = typer.Option(
        300, "--min-interval",
        help="Minimum seconds between successful opens.",
    ),
    interval: int = typer.Option(
        30, "--interval",
        help="Poll interval in seconds.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Don't sign — print intended actions only.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip the startup confirmation prompt.",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Use mainnet (default: testnet).",
    ),
):
    """Watch HL perp positions and auto-open a CFI v2 hedge when conditions fire.

    Agent-controlled. Foreground polling loop — Ctrl-C to exit cleanly.

    Trigger (Phase 1): for each watched coin, if there's no active hedge in
    `~/.nunchi/hedges.json` AND the user's HL perp notional (summed across
    cross + isolated) exceeds `--notional-trigger`, auto-open a 1/L BTCSWP
    long via the existing `_build_proposal` + `place_order` path.

    Decision logic + safety caps are pure (see `modules/hedge_auto.py`).

    Persistent state lives at `~/.nunchi/hedge-auto-state.json`. Every
    decision (action + skip) is appended to `~/.nunchi/hedge-auto.log`.
    """
    _boot_cli()
    import time as _time

    from cli.config import TradingConfig
    from cli.display import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW
    from modules.hedge_auto import (
        HedgeAutoStateStore,
        HedgePolicy,
        active_hedge_coins_from_store,
        append_audit_log,
        compute_hedge_open_action,
        sum_perp_notional_for_coin,
        today_utc_iso,
    )
    from strategies.cfi_hedge import get_cfi_profile
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    # Parse + validate the coin list.
    coin_tuple = tuple(c.strip().upper() for c in coins.split(",") if c.strip())
    unsupported = [c for c in coin_tuple if get_cfi_profile(c) is None]
    if unsupported:
        typer.echo(
            f"{RED}Error: no CFI v2 profile deployed for: {', '.join(unsupported)}{RESET}",
            err=True,
        )
        raise typer.Exit(2)

    policy = HedgePolicy(
        notional_trigger_usd=notional_trigger,
        coins=coin_tuple,
        max_hedge_notional_usd=max_hedge_notional,
        max_per_day_actions=max_per_day,
        min_interval_seconds=min_interval,
    )
    try:
        policy.validate()
    except ValueError as e:
        typer.echo(f"{RED}invalid policy: {e}{RESET}", err=True)
        raise typer.Exit(2)

    cfg = TradingConfig()
    private_key = cfg.get_private_key()
    raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
    hl = DirectHLProxy(raw_hl)
    addr = hl.get_account_state().get("address", "")
    network = "mainnet" if mainnet else "testnet"

    typer.echo(f"{BOLD}=== hl hedge auto ==={RESET}")
    typer.echo(f"  Address:           {addr or '?'}")
    typer.echo(f"  Network:           {network}")
    typer.echo(f"  Coins:             {CYAN}{', '.join(coin_tuple)}{RESET}")
    typer.echo(f"  Notional trigger:  ${notional_trigger:,.2f}")
    typer.echo(f"  Max hedge / open:  ${max_hedge_notional:,.2f}")
    typer.echo(f"  Max actions / day: {max_per_day}")
    typer.echo(f"  Min interval:      {min_interval}s")
    typer.echo(f"  Poll:              {interval}s")
    typer.echo(f"  Mode:              {YELLOW + 'DRY-RUN' + RESET if dry_run else GREEN + 'LIVE' + RESET}")
    typer.echo(f"  State file:        ~/.nunchi/hedge-auto-state.json")
    typer.echo(f"  Audit log:         ~/.nunchi/hedge-auto.log")
    typer.echo("")

    if not yes:
        if not typer.confirm("Start watching?"):
            raise typer.Exit(0)

    append_audit_log(
        f"START coins={list(coin_tuple)} notional-trigger={notional_trigger} "
        f"max-hedge-notional={max_hedge_notional} max-per-day={max_per_day} "
        f"dry-run={dry_run} network={network} address={addr}"
    )

    store = HedgeAutoStateStore.default()
    daily = store.load()
    typer.echo(
        f"{DIM}Loaded daily state: {daily.actions_today} actions on {daily.date_iso}{RESET}\n"
    )
    typer.echo(f"{DIM}Polling every {interval}s — Ctrl-C to exit.{RESET}\n")

    try:
        while True:
            now_ms = int(_time.time() * 1000)
            daily = daily.reset_if_new_day(today_utc_iso())

            try:
                state = hl.get_account_state()
            except Exception as e:
                msg = f"poll-error: {e}"
                append_audit_log(msg)
                typer.echo(f"{RED}{msg}{RESET}", err=True)
                _time.sleep(interval)
                continue

            if not state:
                msg = "poll-error: empty account state"
                append_audit_log(msg)
                typer.echo(f"{RED}{msg}{RESET}", err=True)
                _time.sleep(interval)
                continue

            positions = state.get("positions", []) or []
            existing_hedges = _load_hedges()
            active_coins = active_hedge_coins_from_store(existing_hedges)

            ts = _time.strftime("%H:%M:%S")
            any_action = False

            for coin in policy.coins:
                notional = sum_perp_notional_for_coin(positions, coin)
                action, reason = compute_hedge_open_action(
                    coin=coin,
                    perp_notional_usd=notional,
                    active_hedge_coins=active_coins,
                    profile_vol_mult_l=get_cfi_profile(coin).vol_mult_l,
                    policy=policy,
                    daily=daily,
                    now_ms=now_ms,
                )

                if action is None:
                    append_audit_log(reason)
                    typer.echo(f"{DIM}[{ts}] {coin} {reason}{RESET}")
                    continue

                any_action = True
                marker = f"{YELLOW}[DRY-RUN]{RESET}" if dry_run else f"{GREEN}[FIRE]{RESET}"
                typer.echo(f"[{ts}] {marker} {BOLD}{action.reason}{RESET}")

                if dry_run:
                    append_audit_log(f"DRY-RUN would open: {action.reason}")
                    continue

                # Real fire — build proposal + place order via existing helper.
                try:
                    proposal, snapshot = _build_proposal(hl, coin)
                except typer.Exit:
                    msg = f"build-proposal-failed for {coin}; skipping"
                    append_audit_log(msg)
                    typer.echo(f"{RED}{msg}{RESET}", err=True)
                    continue

                wire_px = snapshot.oracle_px or proposal.profile.baseline_b0
                # Size to the agent-decided notional (which may be < proposal.hedge_notional_usd
                # because of caps). Re-derive size from the action.
                size = action.hedge_notional_usd / wire_px
                is_buy = proposal.legs[1].side == "long"
                slippage = 1.002 if is_buy else 0.998
                price = wire_px * slippage

                fill = hl.place_order(
                    instrument=proposal.profile.cfi_instrument,
                    side="buy" if is_buy else "sell",
                    size=size,
                    price=price,
                    tif="Ioc",
                )

                if fill is None:
                    msg = (
                        f"HL-rejected/no-fill for {coin}; intended ${action.hedge_notional_usd:.2f}"
                    )
                    append_audit_log(msg)
                    typer.echo(f"{RED}{msg}{RESET}", err=True)
                    continue

                # Persist HedgeJob (same shape as `hl hedge execute`).
                job_id = f"HEDGE-{now_ms}"
                job = {
                    "id": job_id,
                    "user_address": proposal.user_address,
                    "coin": proposal.source_position.coin,
                    "instrument": proposal.profile.cfi_instrument,
                    "perp_notional_usd": proposal.source_position.notional_usd,
                    "hedge_notional_usd": action.hedge_notional_usd,
                    "locked_k2_apy": proposal.k_fixed_apy,
                    "current_funding_apy": proposal.current_funding_apy,
                    "oid": str(fill.oid),
                    "fill_price": fill.price,
                    "fill_quantity": fill.quantity,
                    "submitted_at_ms": now_ms,
                    "status": "active",
                    "cumulative_savings_usd": 0.0,
                    "last_sample_at_ms": now_ms,
                    "network": "mainnet" if mainnet else "testnet",
                    "opened_by": "agent",
                    "agent_action_reason": action.reason,
                }
                hedges_all = _load_hedges()
                hedges_all.insert(0, job)
                _save_hedges(hedges_all)

                daily = daily.record(action, now_ms)
                store.save(daily)
                active_coins = active_hedge_coins_from_store(hedges_all)  # refresh

                msg = (
                    f"FIRED {coin} hedge=${action.hedge_notional_usd:.2f} oid={fill.oid} "
                    f"actions-today={daily.actions_today}/{policy.max_per_day_actions}"
                )
                append_audit_log(msg)
                typer.echo(f"{GREEN}{msg}{RESET}")

            if not any_action:
                # one-line summary if all coins skipped (avoid empty cycles)
                pass

            _time.sleep(interval)
    except KeyboardInterrupt:
        append_audit_log("STOP (KeyboardInterrupt)")
        typer.echo(f"\n{DIM}Stopped. State saved to ~/.nunchi/hedge-auto-state.json{RESET}")
        store.save(daily)
