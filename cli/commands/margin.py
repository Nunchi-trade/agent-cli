"""hl margin — HL collateral adjustments (cross + isolated, main + HIP-3).

Feature surface:

    hl margin status [--coin COIN]                   show cross + isolated breakdown
    hl margin deposit AMOUNT [--dex yex]             spot→perp (usdClassTransfer) or
                                                     perp→yex (sendAsset)
    hl margin withdraw AMOUNT [--dex yex]            reverse of deposit
    hl margin isolated COIN AMOUNT [--remove]        updateIsolatedMargin add/remove
    hl margin dexes                                  list HIP-3 sub-DEXes
    hl margin auto-topup [--dry-run]                 agent-controlled auto-topup loop

All signing flows through `DirectHLProxy` → HL Python SDK — same path as
`hl trade` and `hl hedge`.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

margin_app = typer.Typer(
    name="margin",
    help="HL collateral adjustments — cross deposits, sub-DEX transfers, isolated margin.",
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


def _open_hl(mainnet: bool):
    from cli.config import TradingConfig
    from cli.hl_adapter import DirectHLProxy
    from parent.hl_proxy import HLProxy

    cfg = TradingConfig()
    raw_hl = HLProxy(private_key=cfg.get_private_key(), testnet=not mainnet)
    return DirectHLProxy(raw_hl)


# ─── status ──────────────────────────────────────────────────────────────────


@margin_app.command("status")
def status_cmd(
    coin: Optional[str] = typer.Option(None, "--coin", help="Filter isolated positions to one coin"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """Show cross-account margin summary + isolated positions."""
    _boot_cli()
    from cli.hedge_display import margin_status_block

    hl = _open_hl(mainnet)
    state = hl.get_account_state()
    if not state:
        typer.echo("Error: could not fetch HL account state", err=True)
        raise typer.Exit(1)

    isolated = []
    for entry in state.get("positions", []):
        pos = entry.get("position", {})
        lev = pos.get("leverage") or {}
        if lev.get("type") != "isolated":
            continue
        c = (pos.get("coin", "") or "").upper()
        if coin and c != coin.upper():
            continue
        isolated.append(pos)

    typer.echo(margin_status_block(state, isolated, mainnet=mainnet))


# ─── deposit ─────────────────────────────────────────────────────────────────


@margin_app.command("deposit")
def deposit_cmd(
    amount: float = typer.Argument(..., help="USDC amount (decimal)"),
    dex: Optional[str] = typer.Option(
        None,
        "--dex",
        help="HIP-3 sub-DEX (e.g. 'yex'). Omit for spot→main-perp.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirm"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """Move USDC into perp (or into a HIP-3 sub-DEX).

    Without --dex: spot → main-perp via usdClassTransfer.
    With --dex yex: main-perp → yex via sendAsset (prerequisite to
    trading BTCSWP / VXX / US3M).
    """
    _boot_cli()
    if amount <= 0:
        typer.echo("Amount must be positive", err=True)
        raise typer.Exit(2)

    hl = _open_hl(mainnet)
    addr = hl.get_account_state().get("address", "")
    network = "mainnet" if mainnet else "testnet"

    if dex is None or dex == "":
        action_label = f"Deposit ${amount:,.2f} USDC: spot → main perp"
    else:
        action_label = f"Deposit ${amount:,.2f} USDC: main perp → {dex}"

    typer.echo(f"{action_label} ({network})")
    if not yes and not typer.confirm("Sign + submit?"):
        typer.echo("Aborted.")
        raise typer.Exit(0)

    if dex is None or dex == "":
        result = hl.usd_class_transfer(amount=amount, to_perp=True)
    else:
        result = hl.send_asset(
            destination=addr,
            source_dex="",
            destination_dex=dex,
            token="USDC",
            amount=amount,
        )
    _echo_result("deposit", result)


# ─── withdraw ────────────────────────────────────────────────────────────────


@margin_app.command("withdraw")
def withdraw_cmd(
    amount: float = typer.Argument(..., help="USDC amount (decimal)"),
    dex: Optional[str] = typer.Option(
        None,
        "--dex",
        help="HIP-3 sub-DEX (e.g. 'yex'). Omit for main-perp→spot.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirm"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """Move USDC out of perp (or out of a HIP-3 sub-DEX)."""
    _boot_cli()
    if amount <= 0:
        typer.echo("Amount must be positive", err=True)
        raise typer.Exit(2)

    hl = _open_hl(mainnet)
    addr = hl.get_account_state().get("address", "")
    network = "mainnet" if mainnet else "testnet"

    if dex is None or dex == "":
        action_label = f"Withdraw ${amount:,.2f} USDC: main perp → spot"
    else:
        action_label = f"Withdraw ${amount:,.2f} USDC: {dex} → main perp"

    typer.echo(f"{action_label} ({network})")
    if not yes and not typer.confirm("Sign + submit?"):
        typer.echo("Aborted.")
        raise typer.Exit(0)

    if dex is None or dex == "":
        result = hl.usd_class_transfer(amount=amount, to_perp=False)
    else:
        result = hl.send_asset(
            destination=addr,
            source_dex=dex,
            destination_dex="",
            token="USDC",
            amount=amount,
        )
    _echo_result("withdraw", result)


# ─── isolated ────────────────────────────────────────────────────────────────


@margin_app.command("isolated")
def isolated_cmd(
    coin: str = typer.Argument(..., help="Position coin (e.g. BTC, yex:BTCSWP)"),
    amount: float = typer.Argument(..., help="USDC amount (decimal)"),
    remove: bool = typer.Option(False, "--remove", help="Remove margin instead of adding"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirm"),
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """Add or remove isolated margin on a specific position.

    For HIP-3 positions, pass the prefixed name (e.g. 'yex:BTCSWP'). The
    SDK resolves the asset index automatically.
    """
    _boot_cli()
    if amount <= 0:
        typer.echo("Amount must be positive", err=True)
        raise typer.Exit(2)

    hl = _open_hl(mainnet)
    network = "mainnet" if mainnet else "testnet"
    signed = -amount if remove else amount
    verb = "Remove" if remove else "Add"

    typer.echo(f"{verb} ${amount:,.2f} USDC isolated margin on {coin} ({network})")
    if not yes and not typer.confirm("Sign + submit?"):
        typer.echo("Aborted.")
        raise typer.Exit(0)

    result = hl.update_isolated_margin(amount_usd=signed, coin=coin)
    _echo_result("isolated", result)


# ─── dexes ───────────────────────────────────────────────────────────────────


@margin_app.command("dexes")
def dexes_cmd(
    mainnet: bool = typer.Option(False, "--mainnet", help="Use mainnet (default: testnet)"),
):
    """List HIP-3 sub-DEXes exposed by HL (e.g. 'yex')."""
    _boot_cli()
    hl = _open_hl(mainnet)
    names = hl.list_hip3_dexes()
    if not names:
        typer.echo("No HIP-3 sub-DEXes returned by HL.")
        return
    typer.echo("HIP-3 sub-DEXes:")
    for n in names:
        typer.echo(f"  - {n}")


# ─── auto-topup (agent-controlled) ───────────────────────────────────────────


@margin_app.command("auto-topup")
def auto_topup_cmd(
    util_trigger: float = typer.Option(
        0.70, "--util-trigger",
        help="Top up when cross-account utilization exceeds this fraction (0.70 = 70%).",
    ),
    util_target: float = typer.Option(
        0.50, "--util-target",
        help="Target utilization after top-up (0.50 = 50%). Must be < util-trigger.",
    ),
    max_per_topup: float = typer.Option(
        1000.0, "--max-per-topup",
        help="Hard ceiling on any single top-up amount (USD).",
    ),
    max_per_day: float = typer.Option(
        10000.0, "--max-per-day",
        help="Hard ceiling on cumulative top-ups per UTC day (USD).",
    ),
    min_interval: int = typer.Option(
        60, "--min-interval",
        help="Minimum seconds between successful top-ups. Prevents rapid-fire.",
    ),
    min_source_balance: float = typer.Option(
        100.0, "--min-source-balance",
        help="Never drain spot USDC below this (USD).",
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
    """Watch margin state and auto-top-up the perp account from spot.

    Agent-controlled. Foreground polling loop — Ctrl-C to exit cleanly.

    Decision logic + safety caps are pure (see `execution/margin_auto.py`):
      - Triggers when utilization > util-trigger
      - Sizes deposit so utilization returns to util-target
      - Clipped by per-topup cap, daily cap, source-floor
      - Skips when min-interval not met or amount < $0.50

    Persistent state (daily counters, last-action timestamp) lives at
    `~/.nunchi/margin-auto-state.json` and resets at UTC midnight.
    Every decision (action + skip) is logged to `~/.nunchi/margin-auto.log`.
    """
    _boot_cli()
    import time
    from cli.display import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW
    from execution.margin_auto import (
        AccountReading,
        MarginAutoStateStore,
        TopupPolicy,
        append_audit_log,
        compute_topup_action,
        today_utc_iso,
    )

    policy = TopupPolicy(
        util_trigger=util_trigger,
        util_target=util_target,
        max_per_topup_usd=max_per_topup,
        max_per_day_usd=max_per_day,
        min_interval_seconds=min_interval,
        min_source_balance_usd=min_source_balance,
    )
    try:
        policy.validate()
    except ValueError as e:
        typer.echo(f"{RED}invalid policy: {e}{RESET}", err=True)
        raise typer.Exit(2)

    hl = _open_hl(mainnet)
    addr = hl.get_account_state().get("address", "")
    network = "mainnet" if mainnet else "testnet"

    # Banner
    typer.echo(f"{BOLD}=== hl margin auto-topup ==={RESET}")
    typer.echo(f"  Address:     {addr or '?'}")
    typer.echo(f"  Network:     {network}")
    typer.echo(f"  Trigger:     util > {CYAN}{util_trigger * 100:.1f}%{RESET}")
    typer.echo(f"  Target:      util ≤ {CYAN}{util_target * 100:.1f}%{RESET}")
    typer.echo(f"  Per-topup:   max ${max_per_topup:,.2f}")
    typer.echo(f"  Per-day:     max ${max_per_day:,.2f}")
    typer.echo(f"  Min-source:  ${min_source_balance:,.2f}")
    typer.echo(f"  Min-interval: {min_interval}s")
    typer.echo(f"  Poll:        {interval}s")
    typer.echo(f"  Mode:        {YELLOW + 'DRY-RUN' + RESET if dry_run else GREEN + 'LIVE' + RESET}")
    typer.echo(f"  State file:  ~/.nunchi/margin-auto-state.json")
    typer.echo(f"  Audit log:   ~/.nunchi/margin-auto.log")
    typer.echo("")

    if not yes:
        if not typer.confirm("Start watching?"):
            raise typer.Exit(0)

    append_audit_log(
        f"START util-trigger={util_trigger} util-target={util_target} "
        f"max-per-topup={max_per_topup} max-per-day={max_per_day} "
        f"dry-run={dry_run} network={network} address={addr}"
    )

    store = MarginAutoStateStore.default()
    daily = store.load()
    typer.echo(
        f"{DIM}Loaded daily state: spent ${daily.spent_today_usd:.2f}, "
        f"{daily.actions_today} actions on {daily.date_iso}{RESET}\n"
    )

    typer.echo(f"{DIM}Polling every {interval}s — Ctrl-C to exit.{RESET}\n")
    try:
        while True:
            now_ms = int(time.time() * 1000)
            daily = daily.reset_if_new_day(today_utc_iso())

            try:
                state = hl.get_account_state()
            except Exception as e:
                msg = f"poll-error: {e}"
                append_audit_log(msg)
                typer.echo(f"{RED}{msg}{RESET}", err=True)
                time.sleep(interval)
                continue

            if not state:
                msg = "poll-error: empty account state"
                append_audit_log(msg)
                typer.echo(f"{RED}{msg}{RESET}", err=True)
                time.sleep(interval)
                continue

            account_value = float(state.get("account_value", 0) or 0)
            total_margin = float(state.get("total_margin", 0) or 0)
            spot_usdc = float(state.get("spot_usdc", 0) or 0)
            reading = AccountReading(
                account_value_usd=account_value,
                total_margin_used_usd=total_margin,
                spot_usdc_usd=spot_usdc,
            )

            action, reason = compute_topup_action(
                reading=reading,
                policy=policy,
                daily=daily,
                now_ms=now_ms,
            )

            ts = time.strftime("%H:%M:%S")
            util_pct = (total_margin / account_value * 100) if account_value > 0 else 0

            if action is None:
                # Skip — log once, quiet console
                append_audit_log(reason)
                typer.echo(
                    f"{DIM}[{ts}] util={util_pct:.2f}% spot=${spot_usdc:.0f} "
                    f"spent-today=${daily.spent_today_usd:.0f}  {reason}{RESET}"
                )
            else:
                # Action — log, echo, submit (unless dry-run)
                marker = f"{YELLOW}[DRY-RUN]{RESET}" if dry_run else f"{GREEN}[FIRE]{RESET}"
                typer.echo(
                    f"[{ts}] {marker} {BOLD}{action.reason}{RESET}"
                )

                if dry_run:
                    append_audit_log(f"DRY-RUN would submit: {action.reason}")
                else:
                    try:
                        result = hl.usd_class_transfer(
                            amount=action.amount_usd,
                            to_perp=True,
                        )
                    except Exception as e:
                        msg = f"submit-error: {e} (intended: ${action.amount_usd:.2f})"
                        append_audit_log(msg)
                        typer.echo(f"{RED}{msg}{RESET}", err=True)
                        time.sleep(interval)
                        continue

                    status = result.get("status") if isinstance(result, dict) else None
                    if status == "ok":
                        daily = daily.record(action, now_ms)
                        store.save(daily)
                        msg = (
                            f"FIRED amount=${action.amount_usd:.2f} util-before={util_pct:.2f}% "
                            f"spent-today=${daily.spent_today_usd:.2f} actions-today={daily.actions_today}"
                        )
                        append_audit_log(msg)
                        typer.echo(f"{GREEN}{msg}{RESET}")
                    else:
                        msg = f"HL-rejected amount=${action.amount_usd:.2f} response={result}"
                        append_audit_log(msg)
                        typer.echo(f"{RED}{msg}{RESET}", err=True)

            time.sleep(interval)
    except KeyboardInterrupt:
        append_audit_log("STOP (KeyboardInterrupt)")
        typer.echo(f"\n{DIM}Stopped. State saved to ~/.nunchi/margin-auto-state.json{RESET}")
        # Make sure we save the latest daily state on exit
        store.save(daily)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _echo_result(action: str, result):
    """Pretty-print an HL exchange response."""
    from cli.display import BOLD, GREEN, RED, RESET

    if isinstance(result, dict):
        status = result.get("status")
    else:
        status = None
    if status == "ok":
        typer.echo(f"{GREEN}{BOLD}{action.capitalize()} submitted.{RESET} HL response: {result}")
        return
    if status == "err":
        typer.echo(f"{RED}{BOLD}HL error:{RESET} {result.get('error', result)}", err=True)
        raise typer.Exit(1)
    # Some SDK responses just return the inner body. Echo verbatim.
    typer.echo(f"{action.capitalize()} response: {result}")
