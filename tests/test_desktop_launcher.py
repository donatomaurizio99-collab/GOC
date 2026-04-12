from __future__ import annotations

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
    assert args.title == "Goal Ops Console"
    assert args.debug is False


def test_main_returns_nonzero_when_launch_fails(monkeypatch, capsys):
    def _raise(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(desktop, "run_desktop", _raise)
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
