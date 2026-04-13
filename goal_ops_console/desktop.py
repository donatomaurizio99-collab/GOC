from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uvicorn import Config, Server

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


@dataclass(slots=True)
class InstanceLock:
    path: Path
    pid: int


def _pick_port(host: str, explicit_port: int | None) -> int:
    if explicit_port is not None and explicit_port > 0:
        return explicit_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(base_url: str, timeout_seconds: float = 15.0) -> None:
    url = f"{base_url.rstrip('/')}/system/readiness"
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if bool(payload.get("ready")):
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.2)
    if last_error is not None:
        raise RuntimeError(f"Desktop server did not become ready at {url}") from last_error
    raise RuntimeError(f"Desktop server did not become ready at {url}")


def _default_window_state_path() -> Path:
    return Path.home() / ".goal_ops_console" / "window_state.json"


def _default_instance_lock_path() -> Path:
    return Path.home() / ".goal_ops_console" / "desktop.lock"


def _default_diagnostics_dir() -> Path:
    return Path.home() / ".goal_ops_console" / "diagnostics"


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _iso_utc() -> str:
    return datetime.now(UTC).isoformat()


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _unlink_with_retry(path: Path, *, retries: int = 8, base_delay_seconds: float = 0.01) -> bool:
    for attempt in range(max(1, int(retries))):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == retries - 1:
                return False
            time.sleep(base_delay_seconds * (attempt + 1))
    return False


def _write_lock_payload(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def _acquire_instance_lock(path: Path) -> InstanceLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "created_at": _iso_utc(),
    }
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = _read_lock_payload(path)
        existing_pid = _coerce_int(existing.get("pid")) if existing else None
        existing_released = bool(existing and existing.get("released"))
        if existing_released:
            _write_lock_payload(path, payload)
            return InstanceLock(path=path, pid=int(payload["pid"]))
        if existing_pid is not None and not _is_process_running(existing_pid):
            if _unlink_with_retry(path):
                return _acquire_instance_lock(path)
            try:
                _write_lock_payload(path, payload)
                return InstanceLock(path=path, pid=int(payload["pid"]))
            except OSError as exc:
                raise RuntimeError(
                    f"Desktop instance lock exists at {path} and stale lock cleanup failed."
                ) from exc
        if existing_pid is not None:
            raise RuntimeError(
                f"Another Goal Ops Console desktop instance is already running (pid {existing_pid})."
            )
        raise RuntimeError(
            f"Desktop instance lock already exists at {path}. "
            "If no instance is running, delete the lock file and retry."
        )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(json.dumps(payload, ensure_ascii=True))
    except Exception:
        _unlink_with_retry(path, retries=3, base_delay_seconds=0.01)
        raise
    return InstanceLock(path=path, pid=int(payload["pid"]))


def _release_instance_lock(lock: InstanceLock | None) -> None:
    if lock is None:
        return
    existing = _read_lock_payload(lock.path)
    existing_pid = _coerce_int(existing.get("pid")) if existing else None
    if existing_pid is not None and existing_pid != lock.pid:
        return
    if _unlink_with_retry(lock.path):
        return
    try:
        _write_lock_payload(
            lock.path,
            {
                "pid": lock.pid,
                "released": True,
                "released_at": _iso_utc(),
            },
        )
    except OSError:
        return


def _write_crash_report(
    exc: Exception,
    *,
    diagnostics_dir: Path | None = None,
    context: dict[str, Any] | None = None,
) -> Path | None:
    target_dir = diagnostics_dir or _default_diagnostics_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    report_path = target_dir / f"desktop-crash-{_utc_timestamp()}.json"
    payload = {
        "timestamp_utc": _iso_utc(),
        "error_type": exc.__class__.__name__,
        "error": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "context": context or {},
    }
    try:
        report_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        return None
    return report_path


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
    single_instance: bool = True,
    instance_lock_path: str | None = None,
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

    lock = None
    if single_instance:
        lock_path = (
            Path(instance_lock_path).expanduser() if instance_lock_path else _default_instance_lock_path()
        )
        lock = _acquire_instance_lock(lock_path)

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

    base_url = f"http://{host}:{selected_port}"
    url = f"{base_url}/?desktop=1"
    try:
        _wait_until_ready(base_url)

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
        _release_instance_lock(lock)


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
        "--instance-lock-path",
        default=None,
        help="Optional lock file path used to enforce single desktop instance.",
    )
    parser.add_argument(
        "--allow-multiple-instances",
        action="store_true",
        help="Disable single-instance lock (not recommended for production).",
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
            single_instance=(not args.allow_multiple_instances),
            instance_lock_path=args.instance_lock_path,
            window_title=args.title,
            debug=args.debug,
        )
    except Exception as exc:  # pragma: no cover - this is top-level UX handling
        configured_diagnostics_dir = os.getenv("GOAL_OPS_DIAGNOSTICS_DIR", "").strip()
        diagnostics_dir = (
            Path(configured_diagnostics_dir).expanduser() if configured_diagnostics_dir else None
        )
        report_path = _write_crash_report(
            exc,
            diagnostics_dir=diagnostics_dir,
            context={
                "database_url": args.database_url,
                "host": args.host,
                "port": args.port,
                "width": args.width,
                "height": args.height,
                "min_width": args.min_width,
                "min_height": args.min_height,
                "maximized": bool(args.maximized),
            },
        )
        if report_path is not None:
            print(f"[desktop] Crash report saved to: {report_path}")
        print(f"[desktop] Failed to launch: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
