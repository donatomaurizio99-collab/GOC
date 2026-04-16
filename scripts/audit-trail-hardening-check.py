from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from goal_ops_console.config import Settings
from goal_ops_console.services import build_services


def run_check(
    *,
    label: str,
    deployment_profile: str,
    audit_retention_days: int,
    min_audit_retention_days: int,
    seed_entries: int,
    workspace: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = str(deployment_profile).strip().lower() or "production"
    retention_days = max(1, int(audit_retention_days))
    min_retention_days = max(1, int(min_audit_retention_days))
    seed_count = max(1, int(seed_entries))

    workspace.mkdir(parents=True, exist_ok=True)
    db_file = workspace / (
        f"audit-trail-hardening-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        f"-{uuid.uuid4().hex[:8]}.db"
    )

    services = build_services(
        Settings(
            database_url=str(db_file),
            audit_log_retention_days=retention_days,
        )
    )
    for index in range(seed_count):
        services.observability.record_audit(
            action="audit_trail.hardening.seed",
            actor="release-gate",
            status="success",
            entity_type="seed",
            entity_id=str(index),
            details={"index": index, "label": label},
        )
    baseline = services.observability.audit_integrity_status(
        verify_limit=max(200, seed_count * 4),
    )
    services.db.execute(
        """UPDATE audit_log_integrity
           SET entry_hash = ?
           WHERE chain_index = (SELECT MAX(chain_index) FROM audit_log_integrity)""",
        "0" * 64,
    )
    tampered = services.observability.audit_integrity_status(
        verify_limit=max(200, seed_count * 4),
    )

    criteria: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "details": details})

    if profile == "production":
        add(
            "audit_retention_days_minimum",
            retention_days >= min_retention_days,
            f"audit_retention_days={retention_days}, minimum={min_retention_days}",
        )
    else:
        add(
            "non_production_profile",
            True,
            f"deployment_profile={profile!r} (retention hard requirement skipped)",
        )

    add(
        "baseline_integrity_ok",
        bool(baseline.get("ok")),
        f"baseline_ok={baseline.get('ok')!r}",
    )
    baseline_metrics = baseline.get("metrics", {})
    add(
        "baseline_coverage_full",
        int(baseline_metrics.get("missing_integrity_rows") or 0) == 0
        and float(baseline_metrics.get("coverage_percent") or 0.0) >= 100.0,
        (
            "missing_integrity_rows="
            f"{baseline_metrics.get('missing_integrity_rows')}, "
            f"coverage_percent={baseline_metrics.get('coverage_percent')}"
        ),
    )
    add(
        "seeded_entries_recorded",
        int(baseline_metrics.get("total_audit_entries") or 0) >= seed_count,
        f"total_audit_entries={baseline_metrics.get('total_audit_entries')}, seeded={seed_count}",
    )
    tampered_metrics = tampered.get("metrics", {})
    add(
        "tamper_detection_triggered",
        (not bool(tampered.get("ok")))
        and (
            int(tampered_metrics.get("hash_mismatch_count") or 0) > 0
            or int(tampered_metrics.get("previous_link_mismatch_count") or 0) > 0
        ),
        (
            f"tampered_ok={tampered.get('ok')!r}, "
            f"hash_mismatch_count={tampered_metrics.get('hash_mismatch_count')}, "
            "previous_link_mismatch_count="
            f"{tampered_metrics.get('previous_link_mismatch_count')}"
        ),
    )

    failed = [item for item in criteria if not item["passed"]]
    success = len(failed) == 0
    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "deployment_profile": profile,
            "audit_retention_days": retention_days,
            "min_audit_retention_days": min_retention_days,
            "seed_entries": seed_count,
            "workspace": str(workspace),
            "database_file": str(db_file),
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_failed": len(failed),
            "criteria_passed": len(criteria) - len(failed),
            "baseline_total_audit_entries": int(baseline_metrics.get("total_audit_entries") or 0),
            "baseline_chain_entries": int(baseline_metrics.get("chain_entries") or 0),
            "tampered_hash_mismatch_count": int(tampered_metrics.get("hash_mismatch_count") or 0),
            "tampered_link_mismatch_count": int(
                tampered_metrics.get("previous_link_mismatch_count") or 0
            ),
        },
        "criteria": criteria,
        "failed_criteria": failed,
        "baseline_integrity": baseline,
        "tampered_integrity": tampered,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit trail hardening check. Seeds audit events, verifies hash-chain integrity, "
            "then injects tampering and asserts the detector catches it."
        )
    )
    parser.add_argument("--label", default="audit-trail-hardening")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--audit-retention-days", type=int, default=365)
    parser.add_argument("--min-audit-retention-days", type=int, default=90)
    parser.add_argument("--seed-entries", type=int, default=8)
    parser.add_argument("--workspace", default=".tmp/audit-trail-hardening-check")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    workspace = Path(str(args.workspace)).expanduser()
    if not workspace.is_absolute():
        workspace = (project_root / workspace).resolve()

    report = run_check(
        label=str(args.label),
        deployment_profile=str(args.deployment_profile),
        audit_retention_days=int(args.audit_retention_days),
        min_audit_retention_days=int(args.min_audit_retention_days),
        seed_entries=int(args.seed_entries),
        workspace=workspace,
    )

    output_file = None
    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    if report["success"] is False and not bool(args.allow_failure):
        print(
            f"[audit-trail-hardening-check] ERROR: {json.dumps(report, sort_keys=True)}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
