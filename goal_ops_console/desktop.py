from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
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


def _default_window_state_path() -> Path:
    return Path.home() / ".goal_ops_console" / "window_state.json"


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_window_state(
    *,
    path: Path,
    width: int,
    height: int,
    min_width: int,
    min_height: int,
    start_maximized: bool,
) -> dict[str, Any]:
    defaults = {
        "width": max(min_width, int(width)),
        "height": max(min_height, int(height)),
        "x": None,
        "y": None,
        "maximized": bool(start_maximized),
    }
    if not path.exists():
        return defaults

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return defaults

    if not isinstance(payload, dict):
        return defaults

    loaded_width = _coerce_int(payload.get("width"))
    loaded_height = _coerce_int(payload.get("height"))
    loaded_x = _coerce_int(payload.get("x"))
    loaded_y = _coerce_int(payload.get("y"))

    return {
        "width": max(min_width, loaded_width) if loaded_width is not None else defaults["width"],
        "height": max(min_height, loaded_height) if loaded_height is not None else defaults["height"],
        "x": loaded_x,
        "y": loaded_y,
        "maximized": bool(payload.get("maximized", defaults["maximized"])),
    }


def _capture_window_state(
    *,
    window: Any,
    min_width: int,
    min_height: int,
    maximized: bool,
) -> dict[str, Any]:
    raw_width = _coerce_int(getattr(window, "width", None))
    raw_height = _coerce_int(getattr(window, "height", None))

    return {
        "width": max(min_width, raw_width if raw_width is not None else min_width),
        "height": max(min_height, raw_height if raw_height is not None else min_height),
        "x": _coerce_int(getattr(window, "x", None)),
        "y": _coerce_int(getattr(window, "y", None)),
        "maximized": bool(maximized),
    }


def _save_window_state(*, path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def _register_window_event(events: Any, event_name: str, callback: Any) -> None:
    event = getattr(events, event_name, None)
    if event is None:
        return
    try:
        event += callback
    except Exception:
        return


def run_desktop(
    *,
    database_url: str,
    host: str = "127.0.0.1",
    port: int | None = None,
    width: int = 1440,
    height: int = 900,
    min_width: int = 1024,
    min_height: int = 720,
    start_maximized: bool = False,
    remember_window: bool = True,
    window_state_path: str | None = None,
    window_title: str = "Goal Ops Console",
    debug: bool = False,
) -> None:
    safe_min_width = max(640, int(min_width))
    safe_min_height = max(480, int(min_height))
    state_path = Path(window_state_path).expanduser() if window_state_path else _default_window_state_path()
    initial_state = (
        _load_window_state(
            path=state_path,
            width=width,
            height=height,
            min_width=safe_min_width,
            min_height=safe_min_height,
            start_maximized=start_maximized,
        )
        if remember_window
        else {
            "width": max(safe_min_width, int(width)),
            "height": max(safe_min_height, int(height)),
            "x": None,
            "y": None,
            "maximized": bool(start_maximized),
        }
    )

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

    url = f"http://{host}:{selected_port}/?desktop=1"
    try:
        _wait_until_ready(url)

        try:
            import webview  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - dependency is optional
            raise RuntimeError(
                "pywebview is required for desktop mode. Install with: "
                "python -m pip install \"goal-ops-console[desktop]\""
            ) from exc

        create_kwargs: dict[str, Any] = {
            "title": window_title,
            "url": url,
            "width": initial_state["width"],
            "height": initial_state["height"],
            "min_size": (safe_min_width, safe_min_height),
            "text_select": True,
        }
        if initial_state["x"] is not None and initial_state["y"] is not None:
            create_kwargs["x"] = initial_state["x"]
            create_kwargs["y"] = initial_state["y"]

        window = webview.create_window(**create_kwargs)

        maximize_state = {"maximized": bool(initial_state["maximized"])}
        events = getattr(window, "events", None)
        if events is not None:
            _register_window_event(events, "maximized", lambda *_args, **_kwargs: maximize_state.update(maximized=True))
            _register_window_event(events, "restored", lambda *_args, **_kwargs: maximize_state.update(maximized=False))

            if remember_window:
                def _persist_window_state(*_args: Any, **_kwargs: Any) -> None:
                    try:
                        state = _capture_window_state(
                            window=window,
                            min_width=safe_min_width,
                            min_height=safe_min_height,
                            maximized=maximize_state["maximized"],
                        )
                        _save_window_state(path=state_path, state=state)
                    except OSError:
                        return

                _register_window_event(events, "closing", _persist_window_state)

        def _on_webview_start(window_ref: Any) -> None:
            if maximize_state["maximized"]:
                maximize = getattr(window_ref, "maximize", None)
                if callable(maximize):
                    try:
                        maximize()
                    except Exception:
                        return

        webview.start(_on_webview_start, window, debug=debug)
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
        "--min-width",
        type=int,
        default=1024,
        help="Minimum desktop window width.",
    )
    parser.add_argument(
        "--min-height",
        type=int,
        default=720,
        help="Minimum desktop window height.",
    )
    parser.add_argument(
        "--maximized",
        action="store_true",
        help="Open the desktop window maximized.",
    )
    parser.add_argument(
        "--window-state-path",
        default=None,
        help="Optional JSON file path used to persist desktop window position/size.",
    )
    parser.add_argument(
        "--no-window-state",
        action="store_true",
        help="Disable persistent desktop window state.",
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
            min_width=args.min_width,
            min_height=args.min_height,
            start_maximized=args.maximized,
            remember_window=(not args.no_window_state),
            window_state_path=args.window_state_path,
            window_title=args.title,
            debug=args.debug,
        )
    except Exception as exc:  # pragma: no cover - this is top-level UX handling
        print(f"[desktop] Failed to launch: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
