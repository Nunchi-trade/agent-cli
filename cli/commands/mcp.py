"""hl mcp — MCP server for AI agent tool discovery."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

mcp_app = typer.Typer(no_args_is_help=True)


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option("stdio", "--transport", "-t",
                                   help="Transport mode: stdio, sse, or streamable-http"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host for HTTP transports"),
    port: int = typer.Option(8765, "--port", help="Bind port for HTTP transports"),
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
    typer.echo(f"Starting MCP server (transport={transport}, host={host}, port={port}) ...")
    if transport in {"sse", "streamable-http"}:
        server.run(transport=transport, host=host, port=port)
    else:
        server.run(transport=transport)
