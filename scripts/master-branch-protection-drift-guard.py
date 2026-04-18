from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_CHECKS = [
    "Release Gate (Windows)",
    "Security CI Lane",
    "Pytest (Python 3.11)",
    "Pytest (Python 3.12)",
    "Desktop Smoke (Windows)",
]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _run_gh_api(path: str) -> dict[str, Any]:
    command = ["gh", "api", path]
    completed = subprocess.run(command, capture_output=True, text=True)
    _expect(
        completed.returncode == 0,
        f"gh api failed ({completed.returncode}) for '{path}': {completed.stderr.strip()}",
    )
    payload = json.loads(completed.stdout)
    _expect(isinstance(payload, dict), f"Expected JSON object from gh api path '{path}'.")
    return payload


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _parse_required_checks(text: str) -> list[str]:
    checks = [item.strip() for item in str(text).split(",") if item.strip()]
    _expect(checks, "At least one required check must be configured.")
    return checks


def _dedupe_ordered_strings(values: list[str]) -> tuple[list[str], list[str]]:
    deduped: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw).strip()
        if not token:
            continue
        if token in seen:
            duplicates.append(token)
            continue
        seen.add(token)
        deduped.append(token)
    return deduped, duplicates


def _extract_contexts_from_checks(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        return [], []
    contexts: list[str] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        context = str(item.get("context") or "").strip()
        if context:
            contexts.append(context)
    return _dedupe_ordered_strings(contexts)


def _extract_contexts_from_contexts_field(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    raw_contexts = payload.get("contexts")
    if not isinstance(raw_contexts, list):
        return [], []
    contexts: list[str] = [str(item).strip() for item in raw_contexts if str(item).strip()]
    return _dedupe_ordered_strings(contexts)


def run_drift_guard(
    *,
    label: str,
    repo: str,
    branch: str,
    required_checks: list[str],
    required_status_checks_file: Path | None,
    allow_drift: bool,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_required_checks, duplicate_required_checks = _dedupe_ordered_strings(required_checks)
    _expect(resolved_required_checks, "At least one required check must be configured.")

    if required_status_checks_file is not None:
        payload = _load_json_file(required_status_checks_file)
    else:
        payload = _run_gh_api(f"repos/{repo}/branches/{branch}/protection/required_status_checks")

    observed_contexts, observed_context_duplicates = _extract_contexts_from_contexts_field(payload)
    checks_contexts, checks_context_duplicates = _extract_contexts_from_checks(payload)
    observed_required_checks = observed_contexts if observed_contexts else checks_contexts

    missing_required_checks = [item for item in resolved_required_checks if item not in observed_required_checks]
    unexpected_required_checks = [item for item in observed_required_checks if item not in resolved_required_checks]
    checks_and_contexts_aligned = (
        True
        if not observed_contexts or not checks_contexts
        else (set(observed_contexts) == set(checks_contexts))
    )

    criteria = [
        {
            "name": "required_checks_exact_match",
            "passed": bool(not missing_required_checks and not unexpected_required_checks),
            "details": (
                f"missing_required_checks_total={len(missing_required_checks)}, "
                f"unexpected_required_checks_total={len(unexpected_required_checks)}"
            ),
        },
        {
            "name": "required_checks_configuration_deduped",
            "passed": bool(len(observed_context_duplicates) == 0 and len(checks_context_duplicates) == 0),
            "details": (
                f"context_duplicates_total={len(observed_context_duplicates)}, "
                f"checks_context_duplicates_total={len(checks_context_duplicates)}"
            ),
        },
        {
            "name": "contexts_and_checks_payload_aligned",
            "passed": bool(checks_and_contexts_aligned),
            "details": "contexts and checks context sets must match when both are present",
        },
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "repo": repo,
            "branch": branch,
            "required_checks": resolved_required_checks,
            "required_checks_declared_total": int(len(required_checks)),
            "required_checks_unique_total": int(len(resolved_required_checks)),
            "required_checks_duplicates_removed": duplicate_required_checks,
            "allow_drift": bool(allow_drift),
            "required_status_checks_file": str(required_status_checks_file) if required_status_checks_file else None,
            "output_file": str(output_file),
        },
        "metrics": {
            "observed_required_checks_total": int(len(observed_required_checks)),
            "missing_required_checks_total": int(len(missing_required_checks)),
            "unexpected_required_checks_total": int(len(unexpected_required_checks)),
            "contexts_duplicates_total": int(len(observed_context_duplicates)),
            "checks_context_duplicates_total": int(len(checks_context_duplicates)),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "observed": {
            "strict": payload.get("strict"),
            "contexts": observed_contexts,
            "checks_contexts": checks_contexts,
            "contexts_duplicates": observed_context_duplicates,
            "checks_context_duplicates": checks_context_duplicates,
            "observed_required_checks": observed_required_checks,
            "checks_and_contexts_aligned": bool(checks_and_contexts_aligned),
        },
        "drift": {
            "missing_required_checks": missing_required_checks,
            "unexpected_required_checks": unexpected_required_checks,
        },
        "decision": {
            "branch_protection_drift_detected": not bool(success),
            "recommended_action": "branch_protection_in_sync" if success else "branch_protection_drift_detected",
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_drift:
        raise RuntimeError(f"Master branch-protection drift guard failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that branch protection required checks on master exactly match the expected "
            "stability-first check set."
        )
    )
    parser.add_argument("--label", default="master-branch-protection-drift-guard")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--required-checks", default=",".join(DEFAULT_REQUIRED_CHECKS))
    parser.add_argument("--required-status-checks-file")
    parser.add_argument("--allow-drift", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-branch-protection-drift-guard.json")
    args = parser.parse_args(argv)

    required_status_checks_file = Path(str(args.required_status_checks_file)).expanduser() if args.required_status_checks_file else None
    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_drift_guard(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            required_checks=_parse_required_checks(str(args.required_checks)),
            required_status_checks_file=required_status_checks_file,
            allow_drift=bool(args.allow_drift),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[master-branch-protection-drift-guard] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
