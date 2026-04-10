"""hl apex — APEX autonomous strategy commands."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

apex_app = typer.Typer(no_args_is_help=True)


@apex_app.command("run")
def apex_run(
    tick: float = typer.Option(60.0, "--tick", "-t", help="Seconds between ticks"),
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate orders without real HL connection (alias for --mock)"),
    resume: bool = typer.Option(True, "--resume/--fresh", help="Resume from saved state or start fresh"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    max_ticks: int = typer.Option(0, "--max-ticks"),
    budget: float = typer.Option(0, "--budget", help="Override total budget ($)"),
    slots: int = typer.Option(0, "--slots", help="Override max slots"),
    leverage: float = typer.Option(0, "--leverage", help="Override leverage"),
    markets: Optional[str] = typer.Option(
        None, "--markets", "-m",
        help="Comma-separated list of allowed instruments (e.g. VXX-USDYP,US3M-USDYP). "
             "Restricts pulse/radar scans and entries to only these markets. "
             "Required when running in PR-3 dedicated-wallet mode where the agent "
             "is funded on a HIP-3 dex (e.g. yex) and should not scan universal HL perps.",
    ),
    data_dir: str = typer.Option("data/apex", "--data-dir"),
    strategy_names: Optional[str] = typer.Option(
        None, "--strategy-names",
        help="Comma-separated strategy names to run (e.g. regime_mm,funding_momentum). "
             "Overrides MARKET_STRATEGY_MAP auto-routing.",
    ),
):
    """Start APEX autonomous multi-slot strategy."""
    _run_apex(tick=tick, preset=preset, config=config, mock=mock or dry_run,
              resume=resume, mainnet=mainnet, json_output=json_output,
              max_ticks=max_ticks, budget=budget, slots=slots,
              leverage=leverage, markets=markets, data_dir=data_dir,
              strategy_names=strategy_names)


@apex_app.command("once")
def apex_once(
    preset: Optional[str] = typer.Option(None, "--preset", "-p"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
    mock: bool = typer.Option(False, "--mock"),
    mainnet: bool = typer.Option(False, "--mainnet"),
    json_output: bool = typer.Option(False, "--json"),
    data_dir: str = typer.Option("data/apex", "--data-dir"),
):
    """Run a single APEX tick and exit."""
    _run_apex(tick=0, preset=preset, config=config, mock=mock,
              mainnet=mainnet, json_output=json_output, max_ticks=1,
              budget=0, slots=0, leverage=0, data_dir=data_dir, single=True)


@apex_app.command("status")
def apex_status(data_dir: str = typer.Option("data/apex", "--data-dir")):
    """Show current APEX state and positions."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.apex_state import ApexStateStore
    import time as _time

    store = ApexStateStore(path=f"{data_dir}/state.json")
    state = store.load()

    if not state:
        typer.echo("No APEX state found. Run 'hl apex run' first.")
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


@apex_app.command("reconcile")
def apex_reconcile(
    fix: bool = typer.Option(False, "--fix", help="Auto-fix discrepancies (adopt orphans, correct sizes)"),
    data_dir: str = typer.Option("data/apex", "--data-dir"),
    mock: bool = typer.Option(False, "--mock"),
    mainnet: bool = typer.Option(False, "--mainnet"),
):
    """Reconcile APEX state against exchange positions."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.apex_state import ApexStateStore
    from modules.reconciliation import ReconciliationEngine

    store = ApexStateStore(path=f"{data_dir}/state.json")
    state = store.load()
    if not state:
        typer.echo("No APEX state found. Run 'hl apex run' first.")
        raise typer.Exit()

    if mock:
        from cli.hl_adapter import DirectMockProxy
        hl = DirectMockProxy()
    else:
        from cli.hl_adapter import DirectHLProxy
        from cli.config import TradingConfig
        from parent.hl_proxy import HLProxy
        try:
            private_key = TradingConfig().get_private_key()
        except RuntimeError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        hl = DirectHLProxy(HLProxy(private_key=private_key, testnet=not mainnet))

    account = hl.get_account_state()
    positions = account.get("assetPositions", [])
    slot_dicts = [s.to_dict() for s in state.slots]

    engine = ReconciliationEngine()
    discrepancies = engine.reconcile(slot_dicts, positions)

    if not discrepancies:
        typer.echo("All clear — no discrepancies found.")
        return

    typer.echo(f"Found {len(discrepancies)} discrepancy(ies):\n")
    for d in discrepancies:
        icon = "!!" if d.severity == "critical" else " >"
        typer.echo(f"  {icon} [{d.type}] {d.detail}")

    if not fix:
        typer.echo(f"\nRun with --fix to auto-resolve.")


@apex_app.command("archive")
def apex_archive(
    days: int = typer.Option(0, "--days", help="Archive state closed more than N days ago (0=all closed)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be archived"),
    data_dir: str = typer.Option("data/apex", "--data-dir"),
):
    """Archive closed position state files."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.archiver import StateArchiver
    archiver = StateArchiver(archive_dir=f"{data_dir}/archive")
    counts = archiver.archive_old(guard_dir=f"{data_dir}/guard", days_old=days, dry_run=dry_run)

    prefix = "[DRY RUN] " if dry_run else ""
    typer.echo(f"{prefix}Archived: {counts.get('guard', 0)} guard state files")
    typer.echo(f"{prefix}Skipped: {counts.get('skipped', 0)} (active or too recent)")


@apex_app.command("presets")
def apex_presets():
    """List available APEX presets."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.apex_config import APEX_PRESETS

    for name, cfg in APEX_PRESETS.items():
        typer.echo(f"\n{name}:")
        typer.echo(f"  budget: ${cfg.total_budget:,.0f}")
        typer.echo(f"  max_slots: {cfg.max_slots}")
        typer.echo(f"  leverage: {cfg.leverage}x")
        typer.echo(f"  radar_threshold: {cfg.radar_score_threshold}")
        typer.echo(f"  daily_loss_limit: ${cfg.daily_loss_limit:,.0f}")


def _run_apex(tick, preset, config, mock, mainnet, json_output,
              max_ticks, budget, slots, leverage, data_dir, single=False,
              resume=True, markets=None, strategy_names=None):
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.apex_config import ApexConfig, APEX_PRESETS

    if config:
        cfg = ApexConfig.from_yaml(str(config))
    elif preset and preset in APEX_PRESETS:
        cfg = ApexConfig.from_dict(APEX_PRESETS[preset].to_dict())
    else:
        cfg = ApexConfig()

    # Stamp the preset name onto the config so the runner can use it to load
    # matching pulse/radar sub-guard presets at boot.
    if preset:
        cfg.preset_name = preset

    # CLI overrides
    if budget > 0:
        cfg.total_budget = budget
        cfg.margin_per_slot = budget / max(cfg.max_slots, 1)
    if slots > 0:
        cfg.max_slots = slots
        cfg.margin_per_slot = cfg.total_budget / max(slots, 1)
    if leverage > 0:
        cfg.leverage = leverage
    if markets:
        cfg.allowed_instruments = [m.strip() for m in markets.split(",") if m.strip()]
    if strategy_names:
        cfg.strategy_enabled = True
        cfg.strategy_names = [s.strip() for s in strategy_names.split(",") if s.strip()]

    from common.logging_config import configure_logging, log_startup_banner, resolve_obsidian_path

    configure_logging(
        strategy_name=preset or "apex",
        log_dir=str(Path(data_dir).parent / "logs") if data_dir != "data/apex" else "logs",
        json_logs=json_output,
        level=logging.INFO,
    )

    # Auto-detect Obsidian vault if not explicitly configured
    cfg.obsidian_vault_path = resolve_obsidian_path(cfg.obsidian_vault_path)

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

    # Multi-wallet mode: if wallet_config is non-empty, use MultiWalletEngine
    if cfg.wallet_config and not single:
        from cli.multi_wallet_engine import MultiWalletEngine
        from modules.wallet_manager import WalletConfig, WalletManager

        wm = WalletManager.from_yaml_section(cfg.wallet_config)
        typer.echo(f"Multi-wallet mode: {len(wm.wallet_ids)} wallets")

        def _adapter_factory(wc: WalletConfig):
            """Create a VenueAdapter per wallet.  In mock mode every wallet
            shares the mock backend; in live mode each gets its own proxy."""
            if mock:
                from adapters.mock_adapter import MockVenueAdapter
                return MockVenueAdapter()
            else:
                # Live mode: all wallets share the same HL connection for now
                from adapters.hl_adapter import HLVenueAdapter
                return HLVenueAdapter(hl)  # type: ignore[arg-type]

        def _strategy_factory(wc: WalletConfig):
            """Create a per-wallet strategy instance.  Reuses the APEX engine
            strategy via a lightweight TradingEngine adapter — each wallet
            gets its own strategy_id scoped to the wallet."""
            from sdk.strategy_sdk.base import BaseStrategy, StrategyContext
            from common.models import MarketSnapshot, StrategyDecision

            class WalletPassthroughStrategy(BaseStrategy):
                """Thin wrapper that tags strategy_id with wallet name."""
                def __init__(self, wallet_id: str):
                    super().__init__(strategy_id=f"apex_{wallet_id}")
                def on_tick(self, snapshot, context=None):
                    return []  # Decisions come from ApexRunner at higher level

            return WalletPassthroughStrategy(wc.wallet_id)

        mwe = MultiWalletEngine(
            wallet_manager=wm,
            adapter_factory=_adapter_factory,
            strategy_factory=_strategy_factory,
            instrument="ETH-PERP",
            tick_interval=tick,
            dry_run=mock,
            data_dir=data_dir,
            builder=_builder_info,
            max_house_drawdown=cfg.daily_loss_limit * len(wm.wallet_ids),
            max_house_exposure=cfg.total_budget * cfg.leverage * len(wm.wallet_ids),
        )

        log_startup_banner(
            strategy_name=preset or "apex",
            mode="MOCK" if mock else f"LIVE ({'mainnet' if mainnet else 'testnet'})",
            budget=cfg.total_budget,
            slots=cfg.max_slots,
            leverage=cfg.leverage,
            daily_loss_limit=cfg.daily_loss_limit,
            guard_preset=cfg.guard_preset,
            obsidian_enabled=bool(cfg.obsidian_vault_path),
            reflect_interval=cfg.reflect_interval_ticks,
        )

        mwe.run(max_ticks=max_ticks, resume=resume)
        return

    # Single-wallet mode (default): use ApexRunner
    from skills.apex.scripts.standalone_runner import ApexRunner

    runner = ApexRunner(hl=hl, config=cfg, tick_interval=tick,
                        json_output=json_output, data_dir=data_dir,
                        builder=_builder_info, resume=resume)

    log_startup_banner(
        strategy_name=preset or "apex",
        mode="MOCK" if mock else f"LIVE ({'mainnet' if mainnet else 'testnet'})",
        budget=cfg.total_budget,
        slots=cfg.max_slots,
        leverage=cfg.leverage,
        daily_loss_limit=cfg.daily_loss_limit,
        guard_preset=cfg.guard_preset,
        obsidian_enabled=bool(cfg.obsidian_vault_path),
        reflect_interval=cfg.reflect_interval_ticks,
    )

    if single:
        runner.run_once()
    else:
        runner.run(max_ticks=max_ticks)
