"""hl pulse — Pulse capital inflow detection commands."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

pulse_app = typer.Typer(no_args_is_help=True)


@pulse_app.command("run")
def pulse_run(
    tick: float = typer.Option(60.0, "--tick", "-t", help="Seconds between scans"),
    min_volume: float = typer.Option(500_000.0, "--min-volume"),
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    max_scans: int = typer.Option(0, "--max-scans"),
    data_dir: str = typer.Option("data/pulse", "--data-dir"),
):
    """Start continuous Pulse capital inflow detection."""
    _run_pulse(tick=tick, min_volume=min_volume, preset=preset, config=config,
               mock=mock, mainnet=mainnet, json_output=json_output,
               max_scans=max_scans, data_dir=data_dir)


@pulse_app.command("once")
def pulse_once(
    min_volume: float = typer.Option(500_000.0, "--min-volume"),
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    data_dir: str = typer.Option("data/pulse", "--data-dir"),
):
    """Run a single Pulse scan and exit."""
    _run_pulse(tick=0, min_volume=min_volume, preset=preset, config=config,
               mock=mock, mainnet=mainnet, json_output=json_output,
               max_scans=1, data_dir=data_dir, single=True)


@pulse_app.command("status")
def pulse_status(data_dir: str = typer.Option("data/pulse", "--data-dir")):
    """Show last Pulse scan results."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.pulse_state import PulseHistoryStore, PulseResult
    import time as _time

    store = PulseHistoryStore(path=f"{data_dir}/scan-history.json")
    history = store.get_history()

    if not history:
        typer.echo("No Pulse scan history found.")
        raise typer.Exit()

    last = PulseResult.from_dict(history[-1])
    age = (_time.time() * 1000 - last.scan_time_ms) / 1000

    typer.echo(f"Last scan: {age:.0f}s ago  |  Signals: {len(last.signals)}")
    if last.signals:
        for i, sig in enumerate(last.signals[:10], 1):
            typer.echo(f"  {i}. {sig.signal_type} {sig.direction} {sig.asset} "
                       f"conf={sig.confidence:.0f}")
    else:
        typer.echo("  No signals detected.")


@pulse_app.command("presets")
def pulse_presets():
    """List available Pulse presets."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.pulse_config import PULSE_PRESETS

    for name, cfg in PULSE_PRESETS.items():
        typer.echo(f"\n{name}:")
        typer.echo(f"  volume_min_24h: ${cfg.volume_min_24h:,.0f}")
        typer.echo(f"  oi_delta_immediate: {cfg.oi_delta_immediate_pct}%")
        typer.echo(f"  oi_delta_breakout: {cfg.oi_delta_breakout_pct}%")
        typer.echo(f"  volume_surge_ratio: {cfg.volume_surge_ratio}x")


def _run_pulse(tick, min_volume, preset, config, mock, mainnet,
               json_output, max_scans, data_dir, single=False):
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.pulse_config import PulseConfig, PULSE_PRESETS

    if config:
        cfg = PulseConfig.from_yaml(str(config))
    elif preset and preset in PULSE_PRESETS:
        cfg = PulseConfig.from_dict(PULSE_PRESETS[preset].to_dict())
    else:
        cfg = PulseConfig()

    cfg.volume_min_24h = min_volume

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

    typer.echo(f"Min Vol: ${cfg.volume_min_24h:,.0f}  |  "
               f"OI threshold: {cfg.oi_delta_breakout_pct}%")

    from skills.pulse.scripts.standalone_runner import PulseRunner

    runner = PulseRunner(hl=hl, config=cfg, tick_interval=tick,
                         json_output=json_output, data_dir=data_dir)

    if single:
        runner.run_once()
    else:
        runner.run(max_scans=max_scans)
