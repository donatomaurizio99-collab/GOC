from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-staging-soak-readiness-release-gate.json",
    "artifacts/release-gate-stability-final-readiness-release-gate.json",
    "artifacts/p0-closure-report-release-gate.json",
    "artifacts/canary-guardrails-release-gate.json",
]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _criterion(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _validate_rollout_policy(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    raw_stages = policy.get("rollout_stages")
    stages = raw_stages if isinstance(raw_stages, list) else []
    if not stages:
        errors.append("rollout_stages must be a non-empty list")
        return [], errors

    normalized: list[dict[str, Any]] = []
    last_percent = 0
    for idx, raw in enumerate(stages):
        if not isinstance(raw, dict):
            errors.append(f"stage[{idx}] must be an object")
            continue
        name = str(raw.get("name") or "").strip()
        try:
            traffic_percent = int(raw.get("traffic_percent") or 0)
        except (TypeError, ValueError):
            traffic_percent = 0
        try:
            min_observation_minutes = int(raw.get("min_observation_minutes") or 0)
        except (TypeError, ValueError):
            min_observation_minutes = 0

        if not name:
            errors.append(f"stage[{idx}] missing name")
        if traffic_percent <= 0 or traffic_percent > 100:
            errors.append(f"stage[{idx}] has invalid traffic_percent={traffic_percent}")
        if traffic_percent <= last_percent:
            errors.append(
                f"stage[{idx}] traffic_percent must be strictly increasing ({traffic_percent} <= {last_percent})"
            )
        if min_observation_minutes <= 0:
            errors.append(f"stage[{idx}] has invalid min_observation_minutes={min_observation_minutes}")

        last_percent = max(last_percent, traffic_percent)
        normalized.append(
            {
                "name": name,
                "traffic_percent": traffic_percent,
                "min_observation_minutes": min_observation_minutes,
            }
        )

    if normalized:
        final_percent = int(normalized[-1].get("traffic_percent") or 0)
        if final_percent != 100:
            errors.append(f"final rollout stage must end at 100% traffic (observed={final_percent})")

    return normalized, errors


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    required_reports: list[str],
    required_label: str,
    candidate_version: str,
    output_file: Path,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy_payload = _read_json_object(policy_file)
    rollout_stages, policy_errors = _validate_rollout_policy(policy_payload)

    resolved_required_reports = [_resolve_path(project_root, value) for value in required_reports]

    missing_reports: list[str] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    report_records: list[dict[str, Any]] = []
    loaded_payload_by_path: dict[str, dict[str, Any]] = {}

    for report_path in resolved_required_reports:
        if not report_path.exists():
            missing_reports.append(str(report_path))
            continue
        payload = _read_json_object(report_path)
        loaded_payload_by_path[str(report_path)] = payload
        report_label = str(payload.get("label") or "")
        success_value = payload.get("success")
        has_success_flag = isinstance(success_value, bool)
        is_non_green = (not has_success_flag) or (success_value is False)
        label_matches = (not required_label) or (report_label == required_label)
        record = {
            "path": str(report_path),
            "label": report_label,
            "success": bool(success_value) if has_success_flag else None,
            "has_success_flag": bool(has_success_flag),
            "label_matches_required": bool(label_matches),
        }
        report_records.append(record)
        if is_non_green:
            non_green_reports.append(record)
        if not label_matches:
            label_mismatch_reports.append(record)

    canary_report_path = _resolve_path(project_root, "artifacts/canary-guardrails-release-gate.json")
    canary_payload = loaded_payload_by_path.get(str(canary_report_path), {})
    canary_stage_evaluations = (
        canary_payload.get("stage_evaluations") if isinstance(canary_payload.get("stage_evaluations"), list) else []
    )
    canary_decision_result = str((canary_payload.get("decision") or {}).get("result") or "")

    criteria = [
        _criterion(
            "rollout_policy_valid",
            len(policy_errors) == 0,
            f"policy_errors={len(policy_errors)}",
        ),
        _criterion(
            "required_reports_present",
            len(missing_reports) == 0,
            f"missing_reports={len(missing_reports)}",
        ),
        _criterion(
            "required_reports_green",
            len(non_green_reports) == 0,
            f"non_green_reports={len(non_green_reports)}",
        ),
        _criterion(
            "required_reports_label_match",
            len(label_mismatch_reports) == 0,
            f"label_mismatch_reports={len(label_mismatch_reports)}",
        ),
        _criterion(
            "canary_stage_count_matches_rollout_policy",
            len(canary_stage_evaluations) == len(rollout_stages),
            f"canary_stage_count={len(canary_stage_evaluations)}, policy_stage_count={len(rollout_stages)}",
        ),
        _criterion(
            "canary_decision_present",
            canary_decision_result in {"halt", "promote"},
            f"canary_decision_result={canary_decision_result!r}",
        ),
    ]

    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    rollout_plan = {
        "candidate_version": str(candidate_version),
        "stages": rollout_stages,
        "halt_conditions": policy_payload.get("halt_conditions") if isinstance(policy_payload.get("halt_conditions"), list) else [],
        "backout": policy_payload.get("backout") if isinstance(policy_payload.get("backout"), dict) else {},
    }

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "policy_file": str(policy_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_reports": required_reports,
            "required_label": required_label,
            "candidate_version": str(candidate_version),
            "allow_not_ready": bool(allow_not_ready),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "rollout_required_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "rollout_policy_invalid": len(policy_errors),
            "rollout_stage_count": len(rollout_stages),
            "canary_stage_count": len(canary_stage_evaluations),
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed_rc_cut_and_canary_rollout",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "reports": report_records,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "rollout_policy_errors": policy_errors,
        "rollout_plan": rollout_plan,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate RC canary rollout check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate RC cut and staged canary rollout readiness from Stage-Q/P0 evidence and rollout policy."
        )
    )
    parser.add_argument("--label", default="release-gate-rc-canary-rollout-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-candidate-rollout-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--candidate-version", default="0.0.2-rc1")
    parser.add_argument("--output-file", default="artifacts/release-gate-rc-canary-rollout-release-gate.json")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    output_file = _resolve_path(project_root, args.output_file)
    required_reports = _parse_csv_list(args.required_reports)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            required_label=str(args.required_label),
            candidate_version=str(args.candidate_version),
            output_file=output_file,
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-rc-canary-rollout-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
