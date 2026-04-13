from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

import goal_ops_console.desktop as desktop


def test_pick_port_prefers_explicit_port():
    assert desktop._pick_port("127.0.0.1", 8123) == 8123


def test_pick_port_chooses_ephemeral_port_when_none():
    port = desktop._pick_port("127.0.0.1", None)
    assert isinstance(port, int)
    assert port > 0


def test_parse_args_defaults():
    args = desktop._parse_args([])
    assert args.database_url == "goal_ops.db"
    assert args.host == "127.0.0.1"
    assert args.port == 0
    assert args.width == 1440
    assert args.height == 900
    assert args.min_width == 1024
    assert args.min_height == 720
    assert args.maximized is False
    assert args.window_state_path is None
    assert args.instance_lock_path is None
    assert args.allow_multiple_instances is False
    assert args.crash_state_path is None
    assert args.allow_crash_loop is False
    assert args.crash_loop_max_crashes == desktop.DEFAULT_CRASH_LOOP_MAX_CRASHES
    assert args.crash_loop_window_seconds == desktop.DEFAULT_CRASH_LOOP_WINDOW_SECONDS
    assert args.no_window_state is False
    assert args.title == "Goal Ops Console"
    assert args.debug is False


def _local_test_dir() -> Path:
    base = Path(".tmp") / "pytest-local-window-state"
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"case-{time.time_ns()}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def test_load_window_state_uses_defaults_when_file_missing():
    test_dir = _local_test_dir()
    try:
        state = desktop._load_window_state(
            path=test_dir / "missing.json",
            width=1400,
            height=880,
            min_width=1024,
            min_height=720,
            start_maximized=False,
        )
        assert state["width"] == 1400
        assert state["height"] == 880
        assert state["x"] is None
        assert state["y"] is None
        assert state["maximized"] is False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_load_window_state_reads_saved_geometry():
    test_dir = _local_test_dir()
    try:
        state_file = test_dir / "window_state.json"
        state_file.write_text(
            json.dumps(
                {
                    "width": 1600,
                    "height": 1000,
                    "x": 120,
                    "y": 80,
                    "maximized": True,
                }
            ),
            encoding="utf-8",
        )
        state = desktop._load_window_state(
            path=state_file,
            width=1300,
            height=820,
            min_width=1024,
            min_height=720,
            start_maximized=False,
        )
        assert state["width"] == 1600
        assert state["height"] == 1000
        assert state["x"] == 120
        assert state["y"] == 80
        assert state["maximized"] is True
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_capture_window_state_applies_minimums():
    class _Window:
        width = 800
        height = 640
        x = 50
        y = 40

    state = desktop._capture_window_state(
        window=_Window(),
        min_width=1024,
        min_height=720,
        maximized=False,
    )
    assert state["width"] == 1024
    assert state["height"] == 720
    assert state["x"] == 50
    assert state["y"] == 40
    assert state["maximized"] is False


def test_save_window_state_writes_json():
    test_dir = _local_test_dir()
    try:
        state_file = test_dir / "nested" / "window_state.json"
        desktop._save_window_state(
            path=state_file,
            state={
                "width": 1200,
                "height": 800,
                "x": 30,
                "y": 40,
                "maximized": False,
            },
        )
        payload = json.loads(state_file.read_text(encoding="utf-8"))
        assert payload["width"] == 1200
        assert payload["height"] == 800
        assert payload["x"] == 30
        assert payload["y"] == 40
        assert payload["maximized"] is False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_acquire_and_release_instance_lock_roundtrip():
    test_dir = _local_test_dir()
    try:
        lock_path = test_dir / "desktop.lock"
        lock = desktop._acquire_instance_lock(lock_path)
        payload = desktop._read_lock_payload(lock_path)
        assert lock_path.exists()
        assert payload is not None
        assert payload["pid"] == lock.pid

        desktop._release_instance_lock(lock)
        if lock_path.exists():
            released_payload = desktop._read_lock_payload(lock_path)
            assert released_payload is not None
            assert released_payload.get("released") is True
            assert released_payload["pid"] == lock.pid
        else:
            assert lock_path.exists() is False
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_acquire_instance_lock_reclaims_stale_lock(monkeypatch):
    test_dir = _local_test_dir()
    try:
        lock_path = test_dir / "desktop.lock"
        lock_path.write_text(json.dumps({"pid": 424242, "created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")

        monkeypatch.setattr(desktop, "_is_process_running", lambda _pid: False)
        lock = desktop._acquire_instance_lock(lock_path)
        payload = desktop._read_lock_payload(lock_path)

        assert payload is not None
        assert payload["pid"] == os.getpid()
        desktop._release_instance_lock(lock)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_acquire_instance_lock_rejects_active_lock(monkeypatch):
    test_dir = _local_test_dir()
    try:
        lock_path = test_dir / "desktop.lock"
        lock_path.write_text(json.dumps({"pid": 99999, "created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        monkeypatch.setattr(desktop, "_is_process_running", lambda _pid: True)

        with pytest.raises(RuntimeError, match="already running"):
            desktop._acquire_instance_lock(lock_path)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_write_crash_report_writes_json_payload():
    test_dir = _local_test_dir()
    try:
        error = RuntimeError("boom")
        report_path = desktop._write_crash_report(
            error,
            diagnostics_dir=test_dir,
            context={"case": "unit"},
        )
        assert report_path is not None
        assert report_path.exists()
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["error_type"] == "RuntimeError"
        assert payload["error"] == "boom"
        assert payload["context"]["case"] == "unit"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_crash_loop_status_blocks_after_threshold():
    test_dir = _local_test_dir()
    try:
        crash_state_path = test_dir / "crash-state.json"
        now = desktop._iso_utc()
        crash_state_path.write_text(
            json.dumps(
                {
                    "crashes": [
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "A"},
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "B"},
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "C"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        status = desktop._crash_loop_status(
            crash_state_path,
            max_crashes=3,
            window_seconds=600,
        )
        assert status["blocked"] is True
        assert status["recent_count"] == 3
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_crash_loop_status_prunes_old_crashes():
    test_dir = _local_test_dir()
    try:
        crash_state_path = test_dir / "crash-state.json"
        crash_state_path.write_text(
            json.dumps(
                {
                    "crashes": [
                        {
                            "timestamp_utc": "2000-01-01T00:00:00+00:00",
                            "error_type": "RuntimeError",
                            "error": "Old",
                        },
                        {
                            "timestamp_utc": desktop._iso_utc(),
                            "error_type": "RuntimeError",
                            "error": "New",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        status = desktop._crash_loop_status(
            crash_state_path,
            max_crashes=2,
            window_seconds=600,
        )
        assert status["blocked"] is False
        assert status["recent_count"] == 1

        persisted = json.loads(crash_state_path.read_text(encoding="utf-8"))
        assert len(persisted["crashes"]) == 1
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_main_returns_nonzero_when_launch_fails(monkeypatch, capsys):
    def _raise(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(desktop, "run_desktop", _raise)
    monkeypatch.setattr(desktop, "_write_crash_report", lambda *_args, **_kwargs: None)
    exit_code = desktop.main(["--database-url", "demo.db"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "[desktop] Failed to launch:" in captured.out


def test_main_maps_port_zero_to_none(monkeypatch):
    captured_kwargs = {}

    def _fake_run_desktop(**kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(desktop, "run_desktop", _fake_run_desktop)
    exit_code = desktop.main(
        [
            "--database-url",
            "demo.db",
            "--port",
            "0",
            "--width",
            "1200",
            "--height",
            "800",
            "--min-width",
            "1100",
            "--min-height",
            "760",
            "--maximized",
            "--window-state-path",
            "demo-window-state.json",
            "--title",
            "Demo",
            "--debug",
        ]
    )

    assert exit_code == 0
    assert captured_kwargs["database_url"] == "demo.db"
    assert captured_kwargs["port"] is None
    assert captured_kwargs["width"] == 1200
    assert captured_kwargs["height"] == 800
    assert captured_kwargs["min_width"] == 1100
    assert captured_kwargs["min_height"] == 760
    assert captured_kwargs["start_maximized"] is True
    assert captured_kwargs["window_state_path"] == "demo-window-state.json"
    assert captured_kwargs["single_instance"] is True
    assert captured_kwargs["instance_lock_path"] is None
    assert captured_kwargs["remember_window"] is True
    assert captured_kwargs["window_title"] == "Demo"
    assert captured_kwargs["debug"] is True


def test_main_passes_explicit_port(monkeypatch):
    captured_kwargs = {}

    def _fake_run_desktop(**kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(desktop, "run_desktop", _fake_run_desktop)
    exit_code = desktop.main(["--database-url", "demo.db", "--port", "8124"])

    assert exit_code == 0
    assert captured_kwargs["port"] == 8124


def test_main_can_disable_window_state(monkeypatch):
    captured_kwargs = {}

    def _fake_run_desktop(**kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(desktop, "run_desktop", _fake_run_desktop)
    exit_code = desktop.main(["--database-url", "demo.db", "--no-window-state"])

    assert exit_code == 0
    assert captured_kwargs["remember_window"] is False


def test_main_can_disable_single_instance(monkeypatch):
    captured_kwargs = {}

    def _fake_run_desktop(**kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(desktop, "run_desktop", _fake_run_desktop)
    exit_code = desktop.main(
        [
            "--database-url",
            "demo.db",
            "--allow-multiple-instances",
            "--instance-lock-path",
            "custom-desktop.lock",
        ]
    )

    assert exit_code == 0
    assert captured_kwargs["single_instance"] is False
    assert captured_kwargs["instance_lock_path"] == "custom-desktop.lock"


def test_main_blocks_launch_when_crash_loop_active(monkeypatch, capsys):
    test_dir = _local_test_dir()
    try:
        crash_state_path = test_dir / "crash-state.json"
        now = desktop._iso_utc()
        crash_state_path.write_text(
            json.dumps(
                {
                    "crashes": [
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "A"},
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "B"},
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "C"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        called = {"run": False}

        def _fake_run_desktop(**_kwargs):
            called["run"] = True

        monkeypatch.setattr(desktop, "run_desktop", _fake_run_desktop)

        exit_code = desktop.main(
            [
                "--database-url",
                "demo.db",
                "--crash-state-path",
                str(crash_state_path),
                "--crash-loop-max-crashes",
                "3",
                "--crash-loop-window-seconds",
                "600",
            ]
        )
        captured = capsys.readouterr()

        assert exit_code == 2
        assert called["run"] is False
        assert "Crash-loop protection active" in captured.out
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_main_can_bypass_crash_loop_once(monkeypatch):
    test_dir = _local_test_dir()
    try:
        crash_state_path = test_dir / "crash-state.json"
        now = desktop._iso_utc()
        crash_state_path.write_text(
            json.dumps(
                {
                    "crashes": [
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "A"},
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "B"},
                        {"timestamp_utc": now, "error_type": "RuntimeError", "error": "C"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        called = {"run": False}

        def _fake_run_desktop(**_kwargs):
            called["run"] = True

        monkeypatch.setattr(desktop, "run_desktop", _fake_run_desktop)
        exit_code = desktop.main(
            [
                "--database-url",
                "demo.db",
                "--crash-state-path",
                str(crash_state_path),
                "--allow-crash-loop",
            ]
        )

        assert exit_code == 0
        assert called["run"] is True
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_main_records_crash_event_on_failure(monkeypatch):
    test_dir = _local_test_dir()
    try:
        crash_state_path = test_dir / "crash-state.json"
        report_path = test_dir / "desktop-crash-report.json"

        def _raise(**_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(desktop, "run_desktop", _raise)
        monkeypatch.setattr(desktop, "_write_crash_report", lambda *_args, **_kwargs: report_path)

        exit_code = desktop.main(
            [
                "--database-url",
                "demo.db",
                "--crash-state-path",
                str(crash_state_path),
            ]
        )

        assert exit_code == 1
        payload = json.loads(crash_state_path.read_text(encoding="utf-8"))
        assert len(payload["crashes"]) == 1
        assert payload["crashes"][0]["error_type"] == "RuntimeError"
        assert payload["crashes"][0]["report_path"] == str(report_path)
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
