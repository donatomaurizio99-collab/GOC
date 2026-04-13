from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app

STATUS_RANK = {"ok": 0, "degraded": 1, "critical": 2}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _fetch_slo_from_local_app(database_url: str) -> dict[str, Any]:
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        response = client.get("/system/slo")
        _expect(response.status_code == 200, "Local /system/slo returned non-200 status")
        payload = response.json()
    _expect(isinstance(payload, dict), "Local /system/slo payload is not a JSON object")
    return payload


def _fetch_slo_from_url(base_url: str) -> dict[str, Any]:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    _expect(bool(parsed.scheme and parsed.netloc), f"Invalid base URL: {base_url}")
    target = f"{normalized}/system/slo"
    with urllib.request.urlopen(target, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _expect(isinstance(payload, dict), "Remote /system/slo payload is not a JSON object")
    return payload


def _validate_status(payload: dict[str, Any], allowed_status: str) -> dict[str, Any]:
    observed_status = str(payload.get("status") or "").lower()
    _expect(observed_status in STATUS_RANK, f"Unknown SLO status: {observed_status!r}")
    _expect(allowed_status in STATUS_RANK, f"Unknown allowed status: {allowed_status!r}")
    _expect(
        STATUS_RANK[observed_status] <= STATUS_RANK[allowed_status],
        (
            f"SLO status '{observed_status}' exceeds allowed status '{allowed_status}'. "
            f"alerts={payload.get('alerts')}"
        ),
    )
    return {
        "observed_status": observed_status,
        "allowed_status": allowed_status,
        "alert_count": int(payload.get("alert_count") or 0),
        "alerts": payload.get("alerts") or [],
        "timestamp_utc": payload.get("timestamp_utc"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check /system/slo and fail when status exceeds allowed severity."
    )
    parser.add_argument("--database-url", default=":memory:")
    parser.add_argument("--base-url", default="")
    parser.add_argument(
        "--allowed-status",
        choices=("ok", "degraded", "critical"),
        default="ok",
    )
    args = parser.parse_args()

    database_url = str(args.database_url)
    base_url = str(args.base_url).strip()
    if base_url and database_url and database_url != ":memory:":
        print(
            "[slo-alert-check] ERROR: Use either --base-url or --database-url, not both custom values.",
            file=sys.stderr,
        )
        return 1

    try:
        if base_url:
            payload = _fetch_slo_from_url(base_url)
            source = {"mode": "http", "base_url": base_url}
        else:
            payload = _fetch_slo_from_local_app(database_url)
            source = {"mode": "local", "database_url": database_url}
        summary = _validate_status(payload, allowed_status=str(args.allowed_status))
    except Exception as exc:
        print(f"[slo-alert-check] ERROR: {exc}", file=sys.stderr)
        return 1

    output = {
        **summary,
        "source": source,
        "slo": payload,
    }
    print(json.dumps(output, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
