"""Command-line interface for Nixx."""

import argparse
import sys

import httpx
import uvicorn
from rich.console import Console
from rich.table import Table

from nixx.config import NixxConfig
from nixx.tui.app import NixxApp

console = Console()


def _serve(config: NixxConfig, args: argparse.Namespace) -> None:
    """Start the API server."""
    from nixx.server import create_app

    host = args.host or config.host
    port = args.port or config.port
    reload = args.reload or config.reload

    console.print(f"[bold green]nixx[/] starting on [cyan]http://{host}:{port}[/]")
    uvicorn.run(create_app(config), host=host, port=port, reload=reload)


def _status(config: NixxConfig, args: argparse.Namespace) -> None:
    """Check server health."""
    host = args.host or config.host
    port = args.port or config.port
    url = f"http://{host}:{port}/health"

    try:
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except httpx.ConnectError:
        console.print(f"[bold red]unreachable[/] — nothing is listening on {url}")
        sys.exit(1)
    except httpx.HTTPStatusError as exc:
        console.print(f"[bold red]error[/] — server returned {exc.response.status_code}")
        sys.exit(1)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    status_colour = "green" if data.get("status") == "ok" else "yellow"
    table.add_row("status", f"[{status_colour}]{data.get('status', '?')}[/]")
    table.add_row("model", data.get("model", "?"))
    table.add_row("llm", data.get("llm", "?"))

    console.print(table)


def _chat(config: NixxConfig, args: argparse.Namespace) -> None:
    """Launch the chat TUI."""
    # Allow host/port override the same way serve/status do
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    NixxApp(config).run()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nixx",
        description="Self-hosted personal memory system",
    )

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--host", default=None, help="Override server host")
    shared.add_argument("--port", type=int, default=None, help="Override server port")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    serve_p = sub.add_parser("serve", parents=[shared], help="Start the API server")
    serve_p.add_argument(
        "--reload", action="store_true", default=False, help="Enable hot-reload (dev only)"
    )

    sub.add_parser("status", parents=[shared], help="Check server health")

    sub.add_parser("chat", parents=[shared], help="Launch the chat TUI")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    config = NixxConfig()

    if args.command == "serve":
        _serve(config, args)
    elif args.command == "status":
        _status(config, args)
    elif args.command == "chat":
        _chat(config, args)
