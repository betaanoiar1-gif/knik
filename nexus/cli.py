"""NEXUS command line."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app
from .diagnostics import doctor


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nexus", description="NEXUS control plane")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the control plane")
    serve.add_argument("--host", default=os.environ.get("NEXUS_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("NEXUS_PORT", "8787")))
    serve.add_argument("--data-dir", default=os.environ.get("NEXUS_DATA_DIR"))
    serve.add_argument("--inject-ui-token", action="store_true")

    sub.add_parser("doctor", help="print environment and upstream integrity")
    args = parser.parse_args(argv)
    if args.version or args.command is None and args.version:
        print(__version__)
        return
    if args.command in {None, "doctor"} and not args.version and args.command != "serve":
        if args.command is None:
            parser.print_help()
            return
        import json

        print(json.dumps(doctor(), indent=2))
        return
    if args.command == "serve":
        host = args.host
        if host not in {"127.0.0.1", "::1", "localhost"} and not os.environ.get("NEXUS_AUTH_TOKEN"):
            print(
                "Refusing to bind beyond loopback without NEXUS_AUTH_TOKEN. "
                "Set a token, or bind 127.0.0.1.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        data_dir = Path(args.data_dir).resolve() if args.data_dir else None
        app = create_app(
            data_dir,
            host=host,
            auth_token=os.environ.get("NEXUS_AUTH_TOKEN"),
            inject_ui_token=args.inject_ui_token,
        )
        token = app.state.secrets.get("auth_token")
        print(f"NEXUS {__version__} listening on http://{host}:{args.port}", file=sys.stderr)
        if host not in {"127.0.0.1", "::1", "localhost"}:
            print("Control plane is not loopback-only. Use the auth token.", file=sys.stderr)
        if args.inject_ui_token:
            print("UI token injection is enabled for this process only.", file=sys.stderr)
        elif token and host in {"127.0.0.1", "::1", "localhost"}:
            print("Loopback clients are accepted. Remote clients need the auth token.", file=sys.stderr)
        uvicorn.run(app, host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
