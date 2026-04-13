"""Venue-aware RADAR opportunity screening commands."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from cli.radar_market_source import build_radar_market_source
from cli.venue_factory import normalize_venue

import typer

radar_app = typer.Typer(no_args_is_help=True)


@radar_app.command("run")
def radar_run(
    tick: float = typer.Option(
        900.0, "--tick", "-t",
        help="Seconds between scans (default: 15 min)",
    ),
    top_n: int = typer.Option(
        20, "--top-n", "-n",
        help="Number of assets to deep dive",
    ),
    min_volume: float = typer.Option(
        500_000.0, "--min-volume",
        help="Minimum 24h volume to qualify",
    ),
    score_threshold: int = typer.Option(
        150, "--score-threshold",
        help="Minimum final score to show",
    ),
    preset: Optional[str] = typer.Option(
        None, "--preset", "-p",
        help="Config preset (default, aggressive)",
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="YAML config file",
    ),
    mock: bool = typer.Option(
        False, "--mock",
        help="Use mock data (no live venue connection needed)",
    ),
    venue: str = typer.Option(
        "hl", "--venue", "-v",
        help="Venue to scan (hl, paradex)",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Use mainnet (default: testnet)",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Output results as JSON",
    ),
    max_scans: int = typer.Option(
        0, "--max-scans",
        help="Stop after N scans (0 = run forever)",
    ),
    data_dir: str = typer.Option(
        "data/radar", "--data-dir",
        help="Directory for scan history",
    ),
):
    """Start continuous opportunity scanning."""
    _run_radar(
        tick=tick, top_n=top_n, min_volume=min_volume,
        score_threshold=score_threshold, preset=preset,
        config=config, mock=mock, venue=venue, mainnet=mainnet,
        json_output=json_output, max_scans=max_scans,
        data_dir=data_dir,
    )


@radar_app.command("once")
def radar_once(
    top_n: int = typer.Option(20, "--top-n", "-n"),
    min_volume: float = typer.Option(500_000.0, "--min-volume"),
    score_threshold: int = typer.Option(150, "--score-threshold"),
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    venue: str = typer.Option("hl", "--venue", "-v"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    data_dir: str = typer.Option("data/radar", "--data-dir"),
):
    """Run a single scan and exit."""
    _run_radar(
        tick=0, top_n=top_n, min_volume=min_volume,
        score_threshold=score_threshold, preset=preset,
        config=config, mock=mock, venue=venue, mainnet=mainnet,
        json_output=json_output, max_scans=1,
        data_dir=data_dir, single=True,
    )


@radar_app.command("status")
def radar_status(
    data_dir: str = typer.Option("data/radar", "--data-dir"),
):
    """Show last scan results from history."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.radar_state import RadarHistoryStore, RadarResult

    store = RadarHistoryStore(path=f"{data_dir}/scan-history.json")
    history = store.get_history()

    if not history:
        typer.echo("No scan history found.")
        raise typer.Exit()

    last = RadarResult.from_dict(history[-1])
    import time
    scan_age = (time.time() * 1000 - last.scan_time_ms) / 1000

    typer.echo(f"Last scan: {scan_age:.0f}s ago  |  BTC: {last.btc_macro.get('trend', '?')}")
    typer.echo(f"Qualified: {len(last.opportunities)}  |  Disqualified: {len(last.disqualified)}")
    typer.echo()

    if last.opportunities:
        typer.echo(f"{'#':<4} {'Dir':<6} {'Asset':<8} {'Score':<7} {'RSI':<5}")
        typer.echo("-" * 35)
        for i, opp in enumerate(last.opportunities[:10], 1):
            typer.echo(f"{i:<4} {opp.direction:<6} {opp.asset:<8} "
                       f"{opp.final_score:<7.0f} {opp.technicals.get('rsi1h', 50):<5.0f}")
    else:
        typer.echo("No qualifying opportunities in last scan.")


@radar_app.command("presets")
def radar_presets():
    """List available radar presets."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.radar_config import RADAR_PRESETS

    for name, cfg in RADAR_PRESETS.items():
        typer.echo(f"\n{name}:")
        typer.echo(f"  min_volume_24h: ${cfg.min_volume_24h:,.0f}")
        typer.echo(f"  top_n_deep: {cfg.top_n_deep}")
        typer.echo(f"  score_threshold: {cfg.score_threshold}")
        typer.echo(f"  pillar_weights: {cfg.pillar_weights}")


def _run_radar(
    tick, top_n, min_volume, score_threshold, preset, config,
    mock, venue, mainnet, json_output, max_scans, data_dir, single=False,
):
    """Shared setup for run and once commands."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.radar_config import RadarConfig, RADAR_PRESETS

    # Build config
    if config:
        cfg = RadarConfig.from_yaml(str(config))
    elif preset and preset in RADAR_PRESETS:
        cfg = RadarConfig.from_dict(RADAR_PRESETS[preset].to_dict())
    else:
        cfg = RadarConfig()

    cfg.top_n_deep = top_n
    cfg.min_volume_24h = min_volume
    cfg.score_threshold = score_threshold

    normalized_venue = normalize_venue(venue)

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        market_source, mode_label = build_radar_market_source(
            venue=normalized_venue,
            mainnet=mainnet,
            mock=mock,
        )
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Venue: {normalized_venue}")
    typer.echo(f"Mode: {mode_label}")
    typer.echo(f"Top N: {cfg.top_n_deep}  |  Min Vol: ${cfg.min_volume_24h:,.0f}  |  "
               f"Threshold: {cfg.score_threshold}")

    from skills.radar.scripts.standalone_runner import RadarRunner

    runner = RadarRunner(
        hl=market_source,
        config=cfg,
        tick_interval=tick,
        json_output=json_output,
        data_dir=data_dir,
    )

    if single:
        runner.run_once()
    else:
        runner.run(max_scans=max_scans)
