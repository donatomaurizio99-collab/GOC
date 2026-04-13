from __future__ import annotations

import json
import sys
import types
import urllib.request
from urllib.parse import urlparse

from goal_ops_console.desktop import run_desktop


def _assert_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return payload


def main() -> int:
    state: dict[str, str] = {}

    def create_window(*, title: str, url: str, **_kwargs):
        state["url"] = url
        return types.SimpleNamespace(width=1200, height=800, x=50, y=50)

    def start(callback, window, debug=False):  # noqa: ANN001
        if debug:
            raise RuntimeError("Desktop smoke must run with debug disabled")
        if callback is not None:
            callback(window)

        raw_url = state.get("url")
        if not raw_url:
            raise RuntimeError("Desktop smoke did not receive window URL")
        parsed = urlparse(raw_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        readiness = _assert_json(f"{base_url}/system/readiness")
        if not readiness.get("ready"):
            raise RuntimeError(f"Readiness check failed: {readiness}")

        health = _assert_json(f"{base_url}/system/health")
        if "spec_version" not in health:
            raise RuntimeError(f"Health payload missing spec_version: {health}")

    sys.modules["webview"] = types.SimpleNamespace(
        create_window=create_window,
        start=start,
    )
    try:
        run_desktop(
            database_url=":memory:",
            remember_window=False,
            single_instance=False,
            debug=False,
        )
    finally:
        sys.modules.pop("webview", None)

    print("[desktop-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
