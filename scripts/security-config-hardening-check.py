from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _is_memory_database(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    if normalized in {"", ":memory:", "sqlite:///:memory:"}:
        return True
    return normalized.endswith("/:memory:")


def run_check(
    *,
    label: str,
    deployment_profile: str,
    operator_auth_required: bool,
    operator_auth_token: str,
    min_operator_token_length: int,
    database_url: str,
    startup_corruption_recovery_enabled: bool,
    allow_memory_database: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = str(deployment_profile).strip().lower() or "production"
    minimum = max(1, int(min_operator_token_length))
    token = str(operator_auth_token or "").strip()
    criteria: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "details": details})

    if profile == "production":
        add(
            "operator_auth_required",
            bool(operator_auth_required),
            f"operator_auth_required={operator_auth_required!r}",
        )
        add(
            "operator_auth_token_length",
            len(token) >= minimum,
            f"token_length={len(token)}, minimum={minimum}",
        )
        memory_db = _is_memory_database(database_url)
        add(
            "database_not_in_memory",
            (not memory_db) or allow_memory_database,
            f"database_url={database_url!r}, is_memory={memory_db}, allow_memory_database={allow_memory_database!r}",
        )
        add(
            "startup_corruption_recovery_enabled",
            bool(startup_corruption_recovery_enabled),
            f"startup_corruption_recovery_enabled={startup_corruption_recovery_enabled!r}",
        )
    else:
        # For non-production profiles, report values but do not fail by default.
        add(
            "non_production_profile",
            True,
            f"deployment_profile={profile!r} (hard requirements skipped)",
        )

    failed = [item for item in criteria if not item["passed"]]
    success = len(failed) == 0
    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "deployment_profile": profile,
            "operator_auth_required": bool(operator_auth_required),
            "min_operator_token_length": int(minimum),
            "database_url": str(database_url),
            "startup_corruption_recovery_enabled": bool(startup_corruption_recovery_enabled),
            "allow_memory_database": bool(allow_memory_database),
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_failed": len(failed),
            "criteria_passed": len(criteria) - len(failed),
        },
        "criteria": criteria,
        "failed_criteria": failed,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Security/config hardening preflight for production profile. "
            "Checks operator auth, token strength, DB mode, and startup recovery guard."
        )
    )
    parser.add_argument("--label", default="security-config-hardening")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--operator-auth-required", action="store_true")
    parser.add_argument("--operator-auth-token", default="")
    parser.add_argument("--min-operator-token-length", type=int, default=16)
    parser.add_argument("--database-url", default="goal_ops.db")
    parser.add_argument("--startup-corruption-recovery-enabled", action="store_true")
    parser.add_argument("--allow-memory-database", action="store_true")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    report = run_check(
        label=str(args.label),
        deployment_profile=str(args.deployment_profile),
        operator_auth_required=bool(args.operator_auth_required),
        operator_auth_token=str(args.operator_auth_token),
        min_operator_token_length=int(args.min_operator_token_length),
        database_url=str(args.database_url),
        startup_corruption_recovery_enabled=bool(args.startup_corruption_recovery_enabled),
        allow_memory_database=bool(args.allow_memory_database),
    )

    output_file = None
    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (Path(__file__).resolve().parents[1] / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[security-config-hardening-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
