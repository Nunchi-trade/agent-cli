"""hl jobs — CLI commands for Perpetual Agent Jobs."""
from __future__ import annotations

from typing import Optional

import typer

jobs_app = typer.Typer(
    name="jobs",
    help="Perpetual Agent Jobs — register, run, and manage on-chain agent jobs.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@jobs_app.command("list")
def list_jobs_cmd() -> None:
    """Show all available job types."""
    from cli.jobs.registry import list_jobs

    all_jobs = list_jobs()

    header = f"{'ID':<20} {'Name':<25} {'Category':<14} {'Trigger':<18} {'TEE':<6} {'Min Stake':<12}"
    typer.echo(header)
    typer.echo("-" * len(header))

    for job in all_jobs:
        tee_str = "yes" if job.requires_tee else "no"
        stake_str = f"{job.min_stake_eth:.0f} ETH" if job.min_stake_eth > 0 else "none"
        typer.echo(
            f"{job.job_id:<20} "
            f"{job.name:<25} "
            f"{job.category.value:<14} "
            f"{job.trigger.value:<18} "
            f"{tee_str:<6} "
            f"{stake_str:<12}"
        )

    typer.echo(f"\n{len(all_jobs)} jobs registered.")


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


@jobs_app.command("info")
def job_info(
    job_id: str = typer.Argument(..., help="Job identifier (e.g. oracle_updater)"),
) -> None:
    """Show detailed info for a job type."""
    from cli.jobs.registry import get_job

    try:
        job = get_job(job_id)
    except KeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    typer.echo(f"Job:         {job.name}")
    typer.echo(f"ID:          {job.job_id}")
    typer.echo(f"Category:    {job.category.value}")
    typer.echo(f"Description: {job.description}")
    typer.echo(f"Trigger:     {job.trigger.value}")
    if job.trigger_config:
        for k, v in job.trigger_config.items():
            typer.echo(f"  {k}: {v}")
    typer.echo(f"Role:        {job.required_role or 'none (permissionless)'}")
    typer.echo(f"TEE:         {'required' if job.requires_tee else 'not required'}")
    typer.echo(f"Min Stake:   {job.min_stake_eth:.0f} ETH")
    typer.echo(f"Engine:      {job.engine_type}")
    typer.echo(f"Strategy:    {job.strategy_interface}")
    typer.echo(f"Context:     {job.context_template}")

    typer.echo("\nCustody Policy:")
    typer.echo(f"  Destinations:       {', '.join(job.custody.destinations) or 'none'}")
    typer.echo(f"  Selectors:          {', '.join(job.custody.selectors) or 'none'}")
    typer.echo(f"  Value Cap:          {job.custody.value_cap_eth} ETH")
    typer.echo(f"  Rate Limit/Block:   {job.custody.rate_limit_per_block}")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


@jobs_app.command("register")
def register_job(
    job_id: str = typer.Argument(..., help="Job identifier"),
    stake: float = typer.Option(0.0, help="Stake amount in ETH"),
    tee_attest: bool = typer.Option(False, "--tee", help="Include TEE attestation"),
    config: Optional[str] = typer.Option(None, help="Path to job config YAML"),
    mainnet: bool = typer.Option(False, help="Use mainnet"),
) -> None:
    """Register as an agent for a job on-chain."""
    typer.echo("Not yet implemented — design PR only")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@jobs_app.command("run")
def run_job(
    job_id: str = typer.Argument(..., help="Job identifier"),
    config: Optional[str] = typer.Option(None, help="Path to job config YAML"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without submitting txs"),
    mainnet: bool = typer.Option(False, help="Use mainnet"),
) -> None:
    """Start a job engine for the specified job."""
    typer.echo("Not yet implemented — design PR only")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@jobs_app.command("status")
def job_status(
    job_id: Optional[str] = typer.Option(None, help="Filter by job ID"),
) -> None:
    """Show status of running job engines."""
    typer.echo("Not yet implemented — design PR only")


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


@jobs_app.command("claim")
def claim_rewards(
    job_id: str = typer.Argument(..., help="Job identifier"),
    mainnet: bool = typer.Option(False, help="Use mainnet"),
) -> None:
    """Claim accumulated rewards for a job."""
    typer.echo("Not yet implemented — design PR only")


# ---------------------------------------------------------------------------
# deregister
# ---------------------------------------------------------------------------


@jobs_app.command("deregister")
def deregister_job(
    job_id: str = typer.Argument(..., help="Job identifier"),
    mainnet: bool = typer.Option(False, help="Use mainnet"),
) -> None:
    """Deregister from a job and unstake."""
    typer.echo("Not yet implemented — design PR only")
