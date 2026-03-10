"""hl wolf — WOLF autonomous strategy commands."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

wolf_app = typer.Typer(no_args_is_help=True)


@wolf_app.command("run")
def wolf_run(
    tick: float = typer.Option(60.0, "--tick", "-t", help="Seconds between ticks"),
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    resume: bool = typer.Option(True, "--resume/--fresh", help="Resume from saved state or start fresh"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    max_ticks: int = typer.Option(0, "--max-ticks"),
    budget: float = typer.Option(0, "--budget", help="Override total budget ($)"),
    slots: int = typer.Option(0, "--slots", help="Override max slots"),
    leverage: float = typer.Option(0, "--leverage", help="Override leverage"),
    data_dir: str = typer.Option("data/wolf", "--data-dir"),
):
    """Start WOLF autonomous multi-slot strategy."""
    _run_wolf(tick=tick, preset=preset, config=config, mock=mock,
              resume=resume, mainnet=mainnet, json_output=json_output,
              max_ticks=max_ticks, budget=budget, slots=slots,
              leverage=leverage, data_dir=data_dir)


@wolf_app.command("once")
def wolf_once(
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    data_dir: str = typer.Option("data/wolf", "--data-dir"),
):
    """Run a single WOLF tick and exit."""
    _run_wolf(tick=0, preset=preset, config=config, mock=mock,
              mainnet=mainnet, json_output=json_output, max_ticks=1,
              budget=0, slots=0, leverage=0, data_dir=data_dir, single=True)


@wolf_app.command("status")
def wolf_status(data_dir: str = typer.Option("data/wolf", "--data-dir")):
    """Show current WOLF state and positions."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.wolf_state import WolfStateStore
    import time as _time

    store = WolfStateStore(path=f"{data_dir}/state.json")
    state = store.load()

    if not state:
        typer.echo("No WOLF state found. Run 'hl wolf run' first.")
        raise typer.Exit()

    active = state.active_slots()
    typer.echo(f"Ticks: {state.tick_count}  |  Active: {len(active)}/{len(state.slots)}  |  "
               f"Trades: {state.total_trades}")
    typer.echo(f"Daily PnL: ${state.daily_pnl:+.2f}  |  Total PnL: ${state.total_pnl:+.2f}")

    if state.daily_loss_triggered:
        typer.echo("** DAILY LOSS LIMIT TRIGGERED **")

    if active:
        typer.echo(f"\n{'Slot':<5} {'Dir':<6} {'Instrument':<12} {'ROE':<8} {'Source':<16}")
        typer.echo("-" * 50)
        for s in active:
            typer.echo(f"{s.slot_id:<5} {s.direction:<6} {s.instrument:<12} "
                       f"{s.current_roe:+.1f}%{'':>2} {s.entry_source:<16}")
    else:
        typer.echo("\nNo active positions.")


@wolf_app.command("presets")
def wolf_presets():
    """List available WOLF presets."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.wolf_config import WOLF_PRESETS

    for name, cfg in WOLF_PRESETS.items():
        typer.echo(f"\n{name}:")
        typer.echo(f"  budget: ${cfg.total_budget:,.0f}")
        typer.echo(f"  max_slots: {cfg.max_slots}")
        typer.echo(f"  leverage: {cfg.leverage}x")
        typer.echo(f"  radar_threshold: {cfg.radar_score_threshold}")
        typer.echo(f"  daily_loss_limit: ${cfg.daily_loss_limit:,.0f}")


def _run_wolf(tick, preset, config, mock, mainnet, json_output,
              max_ticks, budget, slots, leverage, data_dir, single=False,
              resume=True):
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.wolf_config import WolfConfig, WOLF_PRESETS

    if config:
        cfg = WolfConfig.from_yaml(str(config))
    elif preset and preset in WOLF_PRESETS:
        cfg = WolfConfig.from_dict(WOLF_PRESETS[preset].to_dict())
    else:
        cfg = WolfConfig()

    # CLI overrides
    if budget > 0:
        cfg.total_budget = budget
        cfg.margin_per_slot = budget / max(cfg.max_slots, 1)
    if slots > 0:
        cfg.max_slots = slots
        cfg.margin_per_slot = cfg.total_budget / max(slots, 1)
    if leverage > 0:
        cfg.leverage = leverage

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    if mock:
        from cli.hl_adapter import DirectMockProxy
        hl = DirectMockProxy()
        typer.echo("Mode: MOCK")
    else:
        from cli.hl_adapter import DirectHLProxy
        from cli.config import TradingConfig
        from parent.hl_proxy import HLProxy

        try:
            private_key = TradingConfig().get_private_key()
        except RuntimeError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        raw_hl = HLProxy(private_key=private_key, testnet=not mainnet)
        hl = DirectHLProxy(raw_hl)
        typer.echo(f"Mode: LIVE ({'mainnet' if mainnet else 'testnet'})")

    typer.echo(f"Budget: ${cfg.total_budget:,.0f}  |  Slots: {cfg.max_slots}  |  "
               f"Leverage: {cfg.leverage}x  |  Margin/slot: ${cfg.margin_per_slot:,.0f}")

    # Builder fee
    from cli.config import TradingConfig as _TC
    _tcfg = _TC()
    _bcfg = _tcfg.get_builder_config()
    _builder_info = _bcfg.to_builder_info()
    if _builder_info:
        typer.echo(f"Builder fee: {_bcfg.fee_bps} bps -> {_bcfg.builder_address[:10]}...")

    from skills.wolf.scripts.standalone_runner import WolfRunner

    runner = WolfRunner(hl=hl, config=cfg, tick_interval=tick,
                        json_output=json_output, data_dir=data_dir,
                        builder=_builder_info, resume=resume)

    if single:
        runner.run_once()
    else:
        runner.run(max_ticks=max_ticks)
