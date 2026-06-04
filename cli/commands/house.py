"""hl house — HOUSE-mode fleet launcher.

Spawns and manages a fleet of agent-cli trading subprocesses (one per member),
each tagging Nunchi's builder code on fills. The fleet is GENERIC: it shells out
to `cli.main run <strategy>` (or `cli.main strategy load <name>` for "__load__"
members). It has no dependency on cfi_hedge or the strategy-load runner — those
land in sibling PRs, and members that use them activate once merged.

Supervisor design (simplest-correct):
  - `up` runs a FleetSupervisor in the FOREGROUND, holding the fleet until
    Ctrl-C. On spawn it writes a PID registry to data/cli/fleet/registry.json;
    on shutdown it kills the fleet and clears the registry.
  - `status` / `down` are separate invocations: they read that PID registry and
    probe liveness with os.kill(pid, 0), so they work across processes without a
    long-lived daemon. (`up` owns the supervisor object; `status`/`down` only
    need PIDs.) `down` sends SIGTERM to each live member and clears the registry.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer

house_app = typer.Typer(
    name="house",
    help="HOUSE — fleet launcher for many agent-cli trading subprocesses.",
    no_args_is_help=True,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _registry_path() -> Path:
    return _project_root() / "data" / "cli" / "fleet" / "registry.json"


def _ensure_root_on_path() -> None:
    root = str(_project_root())
    if root not in sys.path:
        sys.path.insert(0, root)


# --------------------------------------------------------------------------- #
# Member spec parsing: --member "name=gold;strategy=engine_mm;market=xyz:GOLD;\
#   preset=house;args=--mock --max-ticks 30"
# Only `strategy` is required. `args` is whitespace-split into extra_args.
# --------------------------------------------------------------------------- #
def _parse_member(spec_str: str):
    from common.fleet_models import FleetMemberSpec

    fields = {}
    for part in spec_str.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise typer.BadParameter(
                f"member field '{part}' is not key=value (in '{spec_str}')"
            )
        key, val = part.split("=", 1)
        fields[key.strip()] = val.strip()

    strategy = fields.get("strategy")
    if not strategy:
        raise typer.BadParameter(f"member '{spec_str}' is missing strategy=")

    extra_args = fields.get("args", "").split() if fields.get("args") else []
    name = fields.get("name") or "·".join(
        p for p in (strategy, fields.get("market")) if p
    )
    return FleetMemberSpec(
        name=name,
        strategy=strategy,
        market=fields.get("market") or None,
        wallet=fields.get("wallet") or None,
        preset=fields.get("preset") or None,
        extra_args=extra_args,
    )


def _write_registry(preset: Optional[str], states: list) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "preset": preset,
        "started_at_ms": int(time.time() * 1000),
        "owner_pid": os.getpid(),
        "members": [
            {
                "id": s.id,
                "name": s.name,
                "strategy": s.strategy,
                "market": s.market,
                "pid": s.pid,
                "status": s.status,
            }
            for s in states
        ],
    }
    path.write_text(json.dumps(payload, indent=2))


def _read_registry() -> Optional[dict]:
    path = _registry_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _clear_registry() -> None:
    path = _registry_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@house_app.command("up")
def house_up(
    preset: Optional[str] = typer.Option(
        None, "--preset", "-p",
        help="Preset .env (configs/presets/<preset>.env) applied to every member",
    ),
    member: List[str] = typer.Option(
        None, "--member", "-m",
        help="Member spec 'strategy=engine_mm;market=xyz:GOLD;args=--mock'. Repeatable.",
    ),
):
    """Spawn the fleet and hold it in the foreground until Ctrl-C."""
    _ensure_root_on_path()
    from cli.fleet_supervisor import FleetSupervisor

    if not member:
        typer.echo("No members specified. Pass one or more --member specs, e.g.:")
        typer.echo(
            "  hl house up -m 'strategy=engine_mm;market=xyz:GOLD;args=--mock --max-ticks 30'"
        )
        raise typer.Exit(1)

    specs = [_parse_member(m) for m in member]
    if preset:
        for s in specs:
            if s.preset is None:
                s.preset = preset

    sup = FleetSupervisor()
    states = sup.spawn_fleet(specs)
    _write_registry(preset, states)

    typer.echo(f"HOUSE fleet up — {len(states)} member(s), preset={preset or 'none'}")
    for s in states:
        market = f" [{s.market}]" if s.market else ""
        typer.echo(f"  {s.status:<8} pid={s.pid or '-':<7} {s.name}{market}")
    typer.echo("\nHolding fleet. Ctrl-C to stop.\n")

    try:
        while True:
            time.sleep(1.0)
            # Refresh registry statuses; exit early if all members are terminal.
            current = sup.get_all()
            _write_registry(preset, current)
            if current and all(
                s.status in ("exited", "errored", "killed") for s in current
            ):
                typer.echo("All members have exited.")
                break
    except KeyboardInterrupt:
        typer.echo("\nShutting down fleet...")
    finally:
        n = sup.kill_all()
        if n:
            typer.echo(f"Sent SIGTERM to {n} member(s).")
        _clear_registry()


@house_app.command("status")
def house_status():
    """List fleet members and recent logs (reads the PID registry)."""
    reg = _read_registry()
    if not reg or not reg.get("members"):
        typer.echo("No fleet registry found. Is `hl house up` running?")
        return

    typer.echo(
        f"{'NAME':<22} {'STRATEGY':<14} {'MARKET':<12} {'PID':>7} {'LIVE':>5}  STATUS"
    )
    typer.echo("-" * 78)
    for m in reg["members"]:
        live = "yes" if _pid_alive(m.get("pid")) else "no"
        typer.echo(
            f"{(m.get('name') or '?'):<22} {(m.get('strategy') or '?'):<14} "
            f"{(m.get('market') or '-'):<12} {str(m.get('pid') or '-'):>7} "
            f"{live:>5}  {m.get('status', '?')}"
        )

    owner = reg.get("owner_pid")
    typer.echo("")
    typer.echo(
        f"owner pid={owner} ({'alive' if _pid_alive(owner) else 'gone'}) | "
        f"registry: {_registry_path()}"
    )
    typer.echo(
        "(`up` holds live stdout/stderr per member in memory; "
        "tail data/cli/*.log or the run's data-dir for full logs.)"
    )


@house_app.command("standing")
def house_standing(
    bypass_cache: bool = typer.Option(
        False, "--no-cache", help="Force re-read of trades.jsonl",
    ),
):
    """Print the Standing board (notional + builder-code accrual from trades)."""
    _ensure_root_on_path()
    from cli.standing_aggregator import StandingAggregator

    agg = StandingAggregator()
    result = agg.get_standing(bypass_cache=bypass_cache)

    typer.echo(f"HOUSE Standing — source: {result.source}")
    typer.echo(
        f"fee rate: {result.fee_rate_tenths_bps} tenths-bps "
        f"({result.fee_rate_tenths_bps / 10:.1f} bps)"
    )
    if result.error:
        typer.echo(f"note: {result.error}")
    typer.echo("")

    typer.echo(
        f"{'MARKET':<14} {'NOTIONAL 24h':>15} {'NOTIONAL 7d':>15} "
        f"{'FILLS 24h':>10} {'BC 24h':>12} {'BC 7d':>12}"
    )
    typer.echo("-" * 82)
    for m in result.markets:
        typer.echo(
            f"{m.market:<14} {m.notional_24h:>15,.2f} {m.notional_7d:>15,.2f} "
            f"{m.fill_count_24h:>10} {m.bc_accrued_24h:>12,.4f} {m.bc_accrued_7d:>12,.4f}"
        )
    typer.echo("-" * 82)
    typer.echo(
        f"{'TOTAL':<14} {result.total_notional_24h:>15,.2f} "
        f"{result.total_notional_7d:>15,.2f} {result.total_fills:>10} "
        f"{result.total_bc_accrued_24h:>12,.4f} {result.total_bc_accrued_7d:>12,.4f}"
    )


@house_app.command("down")
def house_down():
    """Kill all fleet members (SIGTERM) and clear the registry."""
    reg = _read_registry()
    if not reg or not reg.get("members"):
        typer.echo("No fleet registry found. Nothing to stop.")
        return

    killed = 0
    for m in reg["members"]:
        pid = m.get("pid")
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
                typer.echo(f"SIGTERM -> {m.get('name')} (pid={pid})")
            except (ProcessLookupError, PermissionError) as err:
                typer.echo(f"could not signal pid={pid}: {err}")

    # Also signal the foreground `up` owner so it tears down cleanly.
    owner = reg.get("owner_pid")
    if owner and owner != os.getpid() and _pid_alive(owner):
        try:
            os.kill(owner, signal.SIGTERM)
            typer.echo(f"SIGTERM -> up owner (pid={owner})")
        except (ProcessLookupError, PermissionError):
            pass

    _clear_registry()
    typer.echo(f"Stopped {killed} member(s); registry cleared.")
