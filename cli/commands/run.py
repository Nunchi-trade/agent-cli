"""hl run — start autonomous trading loop."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from cli.venue_factory import build_venue_adapter, normalize_venue

import typer


def run_cmd(
    strategy: str = typer.Argument(
        ...,
        help="Strategy name (e.g., 'avellaneda_mm') or path ('module:ClassName')",
    ),
    instrument: str = typer.Option(
        "ETH-PERP", "--instrument", "-i",
        help="Trading instrument (ETH-PERP, VXX-USDYP, US3M-USDYP)",
    ),
    venue: str = typer.Option(
        "hl", "--venue", "-v",
        help="Trading venue (hl, paradex)",
    ),
    tick_interval: float = typer.Option(
        10.0, "--tick", "-t",
        help="Seconds between ticks",
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="YAML config file (overrides CLI flags)",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet",
        help="Use mainnet (default: testnet)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Run strategy against the selected venue's live market data but don't place real orders",
    ),
    max_ticks: int = typer.Option(
        0, "--max-ticks",
        help="Stop after N ticks (0 = run forever)",
    ),
    resume: bool = typer.Option(
        True, "--resume/--fresh",
        help="Resume from saved state or start fresh",
    ),
    data_dir: str = typer.Option(
        "data/cli", "--data-dir",
        help="Directory for state and trade logs",
    ),
    mock: bool = typer.Option(
        False, "--mock",
        help="Use mock market data instead of the selected live venue",
    ),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="LLM model override for claude_agent strategy",
    ),
):
    """Start autonomous trading with a strategy."""
    # Add project root to path for imports
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.config import TradingConfig
    from cli.strategy_registry import resolve_instrument, resolve_strategy_path

    # Load config from YAML if provided, then override with CLI flags
    if config:
        cfg = TradingConfig.from_yaml(str(config))
    else:
        cfg = TradingConfig()

    cfg.strategy = strategy
    cfg.instrument = resolve_instrument(instrument)
    cfg.venue = normalize_venue(venue)
    cfg.tick_interval = tick_interval
    cfg.mainnet = mainnet
    cfg.dry_run = dry_run
    cfg.max_ticks = max_ticks
    cfg.data_dir = data_dir

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve strategy
    strategy_path = resolve_strategy_path(cfg.strategy)

    from sdk.strategy_sdk.loader import load_strategy

    strategy_cls = load_strategy(strategy_path)

    # Pass --model override for LLM strategies
    params = dict(cfg.strategy_params)
    if model:
        params["model"] = model

    # Set up anomaly protection for YEX markets
    anomaly_thread = None
    is_yex = cfg.instrument.startswith("yex:") or cfg.instrument.endswith("-USDYP")
    protection_enabled = is_yex and cfg.protection.get("enabled", is_yex)

    markout_tracker = None

    if protection_enabled:
        try:
            # Ensure anomaly-protection is importable
            anomaly_prot_root = Path.home() / "anomaly-protection"
            if str(anomaly_prot_root) not in sys.path and anomaly_prot_root.exists():
                sys.path.insert(0, str(anomaly_prot_root))

            from anomaly_protection import AnomalyStateStore, AnomalyToxicityScorer, ProtectionConfig, MarkoutTracker
            from anomaly_protection.config import load_protection_config

            store = AnomalyStateStore()

            # Load protection config from file or use defaults
            protection_config_path = cfg.protection.get("config_path")
            if protection_config_path:
                prot_config = load_protection_config(protection_config_path)
            else:
                # Derive pair name from instrument
                pair = cfg.instrument
                if pair.endswith("-USDYP"):
                    pair = "yex:" + pair.replace("-USDYP", "")
                prot_config = ProtectionConfig(pair=pair)
                # Apply any inline config overrides
                if "sensitivity_multiplier" in cfg.protection:
                    prot_config.sensitivity_multiplier = cfg.protection["sensitivity_multiplier"]
                if "max_total_widen_bps" in cfg.protection:
                    prot_config.max_total_widen_bps = cfg.protection["max_total_widen_bps"]

            scorer = AnomalyToxicityScorer(store, prot_config)
            params["toxicity_scorer"] = scorer

            # Create markout tracker for thesis validation
            markout_path = f"{cfg.data_dir}/markouts.jsonl"
            markout_tracker = MarkoutTracker(output_path=markout_path, scorer=scorer)
            typer.echo(f"Markout tracker: logging to {markout_path}")

            # Start anomaly detector in background thread
            from anomaly_protection.sink import ProtectionSink

            sink = ProtectionSink(store)

            def _run_anomaly_detector():
                """Run anomaly detector in its own async event loop (background thread)."""
                import asyncio
                try:
                    anomaly_root = Path.home() / "hl-anomaly-detector"
                    if not anomaly_root.exists():
                        logging.getLogger("protection").warning(
                            "hl-anomaly-detector not found at %s, protection running without detector",
                            anomaly_root,
                        )
                        return

                    import importlib.util
                    sys.path.insert(0, str(anomaly_root))
                    from src.config import DetectorConfig
                    from src.main import AnomalyDetectorApp

                    det_config = DetectorConfig.from_yaml(str(anomaly_root / "config" / "default.yaml"))
                    app = AnomalyDetectorApp(det_config)
                    app.set_raw_event_callback(sink.emit)

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(app.run())
                except Exception as e:
                    logging.getLogger("protection").error("Anomaly detector error: %s", e)

            import threading
            anomaly_thread = threading.Thread(
                target=_run_anomaly_detector,
                name="anomaly-detector",
                daemon=True,
            )
            anomaly_thread.start()
            typer.echo(f"Protection: enabled (pair={prot_config.pair}, sensitivity={prot_config.sensitivity_multiplier}x)")

        except ImportError:
            typer.echo("Protection: anomaly_protection package not installed, skipping")

    strategy_instance = strategy_cls(
        strategy_id=cfg.strategy,
        **params,
    )

    # ── Network guard: prevent wrong-chain accidents ──
    if cfg.venue == "hl":
        if cfg.mainnet:
            env_testnet = os.environ.get("HL_TESTNET", "true").lower()
            if env_testnet == "true":
                typer.echo(
                    "FATAL: --mainnet flag set but HL_TESTNET=true in environment. "
                    "Refusing to start. Set HL_TESTNET=false or source .env.mainnet.",
                    err=True,
                )
                raise typer.Exit(code=1)
        else:
            env_testnet = os.environ.get("HL_TESTNET", "true").lower()
            if env_testnet == "false":
                typer.echo(
                    "FATAL: running in testnet mode but HL_TESTNET=false in environment. "
                    "Pass --mainnet or fix your environment.",
                    err=True,
                )
                raise typer.Exit(code=1)

    try:
        execution_venue, mode_label = build_venue_adapter(
            venue=cfg.venue,
            mainnet=cfg.mainnet,
            mock=mock,
        )
    except (RuntimeError, ValueError, NotImplementedError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    if dry_run:
        mode_label = "DRY RUN"
    typer.echo(f"Mode: {mode_label}")

    typer.echo(f"Strategy: {cfg.strategy} -> {strategy_path}")
    typer.echo(f"Venue: {cfg.venue}")
    typer.echo(f"Instrument: {cfg.instrument}")
    typer.echo(f"Tick interval: {cfg.tick_interval}s")
    if cfg.max_ticks > 0:
        typer.echo(f"Max ticks: {cfg.max_ticks}")
    typer.echo("")

    # Builder fee
    builder_cfg = cfg.get_builder_config()
    builder_info = builder_cfg.to_builder_info()
    if builder_info:
        typer.echo(f"Builder fee: {builder_cfg.fee_bps} bps -> {builder_cfg.builder_address[:10]}...")
    else:
        typer.echo("Builder fee: disabled")

    # Build and run engine
    from cli.engine import TradingEngine

    engine = TradingEngine(
        hl=execution_venue,
        strategy=strategy_instance,
        instrument=cfg.instrument,
        tick_interval=cfg.tick_interval,
        dry_run=cfg.dry_run,
        data_dir=cfg.data_dir,
        risk_limits=cfg.to_risk_limits(),
        builder=builder_info,
    )

    # Attach markout tracker if protection is enabled
    if markout_tracker is not None:
        engine.markout_tracker = markout_tracker

    # Attach Guard if configured
    if cfg.guard and cfg.guard.get("enabled"):
        from modules.guard_config import GuardConfig, PRESETS

        preset_name = cfg.guard.get("preset")
        if preset_name and preset_name in PRESETS:
            guard_cfg = GuardConfig.from_dict(PRESETS[preset_name].to_dict())
        else:
            guard_cfg = GuardConfig.from_dict(cfg.guard)

        if "leverage" in cfg.guard:
            guard_cfg.leverage = float(cfg.guard["leverage"])

        engine.guard_config = guard_cfg
        typer.echo(f"Guard: enabled (preset={preset_name or 'custom'}, tiers={len(guard_cfg.tiers)})")

    engine.run(max_ticks=cfg.max_ticks, resume=resume)
