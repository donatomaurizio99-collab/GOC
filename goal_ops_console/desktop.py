from __future__ import annotations

import argparse
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from uvicorn import Config, Server

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


def _pick_port(host: str, explicit_port: int | None) -> int:
    if explicit_port is not None and explicit_port > 0:
        return explicit_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5):
                return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.2)
    if last_error is not None:
        raise RuntimeError(f"Desktop server did not become ready at {url}") from last_error
    raise RuntimeError(f"Desktop server did not become ready at {url}")


def run_desktop(
    *,
    database_url: str,
    host: str = "127.0.0.1",
    port: int | None = None,
    width: int = 1440,
    height: int = 900,
    window_title: str = "Goal Ops Console",
    debug: bool = False,
) -> None:
    selected_port = _pick_port(host, port)
    app = create_app(Settings(database_url=database_url))
    server = Server(
        Config(
            app=app,
            host=host,
            port=selected_port,
            log_level="warning",
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True, name="goal-ops-desktop-server")
    server_thread.start()

    url = f"http://{host}:{selected_port}/"
    try:
        _wait_until_ready(url)

        try:
            import webview  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise RuntimeError(
                "pywebview is required for desktop mode. Install with: "
                "python -m pip install \"goal-ops-console[desktop]\""
            ) from exc

        webview.create_window(
            title=window_title,
            url=url,
            width=width,
            height=height,
            min_size=(1024, 720),
            text_select=True,
        )
        webview.start(debug=debug)
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Goal Ops Console in a desktop window.")
    parser.add_argument(
        "--database-url",
        default="goal_ops.db",
        help="SQLite database path or URI (default: goal_ops.db).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for the embedded local server (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Server port (0 = auto-pick a free port).",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1440,
        help="Desktop window width.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=900,
        help="Desktop window height.",
    )
    parser.add_argument(
        "--title",
        default="Goal Ops Console",
        help="Desktop window title.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable pywebview debug mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        run_desktop(
            database_url=args.database_url,
            host=args.host,
            port=(args.port if args.port > 0 else None),
            width=args.width,
            height=args.height,
            window_title=args.title,
            debug=args.debug,
        )
    except Exception as exc:  # pragma: no cover - this is top-level UX handling
        print(f"[desktop] Failed to launch: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
