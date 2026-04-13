from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


@dataclass(slots=True)
class ProbeResult:
    label: str
    database_url: str
    database_kind: str
    readiness_ready: bool
    integrity_quick_ok: bool
    integrity_full_ok: bool
    pending_migrations: list[int]

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "database_url": self.database_url,
            "database_kind": self.database_kind,
            "readiness_ready": self.readiness_ready,
            "integrity_quick_ok": self.integrity_quick_ok,
            "integrity_full_ok": self.integrity_full_ok,
            "pending_migrations": list(self.pending_migrations),
        }


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_probe(
    *,
    database_url: str,
    expected_database_kind: str | None,
    label: str,
) -> ProbeResult:
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        readiness = client.get("/system/readiness")
        _expect(readiness.status_code == 200, "Readiness endpoint returned non-200 status")
        readiness_payload = readiness.json()
        _expect(bool(readiness_payload.get("ready")), f"Readiness failed: {readiness_payload}")
        _expect(
            bool(readiness_payload["checks"]["database"]["ok"]),
            f"Database readiness check failed: {readiness_payload}",
        )
        _expect(
            bool(readiness_payload["checks"]["workflow_worker"]["ok"]),
            f"Workflow worker readiness check failed: {readiness_payload}",
        )

        quick = client.get("/system/database/integrity?mode=quick")
        _expect(quick.status_code == 200, "Quick integrity endpoint returned non-200 status")
        quick_payload = quick.json()
        _expect(
            bool(quick_payload["integrity"]["ok"]),
            f"Quick integrity check failed: {quick_payload}",
        )

        full = client.get("/system/database/integrity?mode=full")
        _expect(full.status_code == 200, "Full integrity endpoint returned non-200 status")
        full_payload = full.json()
        _expect(
            bool(full_payload["integrity"]["ok"]),
            f"Full integrity check failed: {full_payload}",
        )

        migrations = full_payload.get("migrations") or {}
        pending_versions = [int(value) for value in (migrations.get("pending_versions") or [])]
        _expect(
            not pending_versions,
            f"Pending schema migrations detected: {pending_versions}",
        )

        database_kind = str((full_payload.get("file") or {}).get("kind") or "unknown")
        if expected_database_kind:
            _expect(
                database_kind == expected_database_kind,
                (
                    f"Unexpected database kind '{database_kind}', "
                    f"expected '{expected_database_kind}'"
                ),
            )

        return ProbeResult(
            label=label,
            database_url=database_url,
            database_kind=database_kind,
            readiness_ready=bool(readiness_payload.get("ready")),
            integrity_quick_ok=bool(quick_payload["integrity"]["ok"]),
            integrity_full_ok=bool(full_payload["integrity"]["ok"]),
            pending_migrations=pending_versions,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Release-gate probe for readiness, integrity, and schema migration state."
    )
    parser.add_argument("--database-url", default=":memory:")
    parser.add_argument("--label", default="probe")
    parser.add_argument(
        "--expected-db-kind",
        choices=("memory", "file"),
        default=None,
    )
    args = parser.parse_args()

    try:
        result = run_probe(
            database_url=str(args.database_url),
            expected_database_kind=args.expected_db_kind,
            label=str(args.label),
        )
    except Exception as exc:
        print(f"[release-gate-probe] ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
