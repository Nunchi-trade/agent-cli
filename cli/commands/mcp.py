"""hl mcp — MCP server for AI agent tool discovery."""
from __future__ import annotations

import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import typer

mcp_app = typer.Typer(no_args_is_help=True)


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option("stdio", "--transport", "-t",
                                   help="Transport mode: stdio or sse"),
):
    """Start MCP server exposing trading tools for AI agents."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from cli.mcp_server import create_mcp_server
    except ImportError:
        typer.echo("ERROR: MCP package not installed. Run: pip install 'yex-trader[mcp]'", err=True)
        raise typer.Exit(1)

    server = create_mcp_server()
    typer.echo(f"Starting MCP server (transport={transport}) ...")
    server.run(transport=transport)


@mcp_app.command("buy-more")
def mcp_buy_more(
    account_id: str = typer.Option("", "--account-id", help="Nunchi account/workspace to top up."),
    plan_id: str = typer.Option("", "--plan-id", help="Subscription plan context for the top-up page."),
    credits: int = typer.Option(100, "--credits", min=1, help="Credit pack size to preselect."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the web-auth top-up page in a browser."),
):
    """Open web-auth to buy more MCP subscription credits."""
    base = os.environ.get("VITE_PAIR_API_URL", "https://web-auth-opal.vercel.app").rstrip("/")
    resolved_account_id = account_id or os.environ.get("NUNCHI_ACCOUNT_ID", "").strip()
    resolved_plan_id = plan_id or os.environ.get("NUNCHI_PLAN_ID", "").strip()
    query = {
        "view": "agent-subscriptions",
        "topUp": "credits",
        "credits": str(credits),
    }
    if resolved_account_id:
        query["accountId"] = resolved_account_id
    if resolved_plan_id:
        query["planId"] = resolved_plan_id
    url = f"{base}/?{urllib.parse.urlencode(query)}"
    typer.echo("Buy more MCP credits in web-auth:")
    typer.echo(url)
    if open_browser:
        webbrowser.open(url)
