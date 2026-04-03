"""hl mcp — MCP server for AI agent tool discovery."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

mcp_app = typer.Typer(no_args_is_help=True)


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option("stdio", "--transport", "-t",
                                   help="Transport mode: stdio or sse"),
    port: int = typer.Option(18790, "--port", "-p",
                             help="Port for SSE transport (ignored for stdio)"),
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
