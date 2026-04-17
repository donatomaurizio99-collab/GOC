from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _powershell_executable() -> str | None:
    for candidate in ("pwsh", "powershell"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _run_manage_rings(
    *,
    manifest_path: Path,
    action: str,
    ring: str = "stable",
    version: str = "",
    reason: str = "",
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    executable = _powershell_executable()
    if executable is None:
        raise RuntimeError("PowerShell executable not found; cannot run manage-desktop-rings.ps1.")

    command = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PROJECT_ROOT / "scripts" / "manage-desktop-rings.ps1"),
        "-ManifestPath",
        str(manifest_path),
        "-Action",
        action,
        "-Ring",
        ring,
    ]
    if version:
        command.extend(["-Version", version])
    if reason:
        command.extend(["-Reason", reason])

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if expect_success and completed.returncode != 0:
        raise RuntimeError(
            "manage-desktop-rings.ps1 failed for action "
            f"{action!r}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    candidate = raw_text.strip()
    _expect(bool(candidate), "Expected JSON output, but command returned empty output.")
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        _expect(start >= 0 and end > start, "Command output did not contain JSON object.")
        payload = json.loads(candidate[start : end + 1])
    _expect(isinstance(payload, dict), "Expected JSON object output from rings command.")
    return payload


def _rings_show(manifest_path: Path, *, ring: str) -> dict[str, Any]:
    completed = _run_manage_rings(
        manifest_path=manifest_path,
        action="show",
        ring=ring,
    )
    return _parse_json_object(completed.stdout)


def _rings_promote(
    manifest_path: Path,
    *,
    ring: str,
    version: str,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run_manage_rings(
        manifest_path=manifest_path,
        action="promote",
        ring=ring,
        version=version,
        expect_success=expect_success,
    )


def _rings_freeze(manifest_path: Path, *, ring: str, reason: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="freeze",
        ring=ring,
        reason=reason,
    )


def _rings_unfreeze(manifest_path: Path, *, ring: str, reason: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="unfreeze",
        ring=ring,
        reason=reason,
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"Required file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _build_stage_evaluations(
    *,
    stages: list[dict[str, Any]],
    statuses: list[str],
    burn_rates: list[float],
    halt_statuses: set[str],
    halt_on_non_ok: bool,
    max_burn_rate: float,
) -> list[dict[str, Any]]:
    normalized_statuses = [item.strip().lower() for item in statuses if item.strip()]
    _expect(normalized_statuses, "At least one mock SLO status is required.")
    for status in normalized_statuses:
        _expect(status in {"ok", "degraded", "critical"}, f"Invalid mock SLO status: {status!r}")

    normalized_burn = [float(item) for item in burn_rates]
    if not normalized_burn:
        normalized_burn = [0.0 for _ in normalized_statuses]
    if len(normalized_burn) < len(normalized_statuses):
        normalized_burn.extend([normalized_burn[-1]] * (len(normalized_statuses) - len(normalized_burn)))

    evaluations: list[dict[str, Any]] = []
    for index, stage in enumerate(stages):
        sample_index = min(index, len(normalized_statuses) - 1)
        status = normalized_statuses[sample_index]
        burn = float(normalized_burn[min(sample_index, len(normalized_burn) - 1)])

        reasons: list[str] = []
        if status in halt_statuses:
            reasons.append("halt_status")
        if halt_on_non_ok and status != "ok":
            reasons.append("non_ok_status")
        if burn > max_burn_rate:
            reasons.append("error_budget_burn_rate")

        evaluations.append(
            {
                "stage_index": index + 1,
                "stage_name": str(stage.get("name") or ""),
                "traffic_percent": int(stage.get("traffic_percent") or 0),
                "status": status,
                "error_budget_burn_rate_percent": round(burn, 4),
                "halt": len(reasons) > 0,
                "halt_reasons": reasons,
            }
        )
    return evaluations


def run_check(
    *,
    label: str,
    deployment_profile: str,
    workspace: Path,
    manifest_path: Path,
    policy_file: Path,
    runbook_file: Path,
    stable_baseline_version: str,
    canary_candidate_version: str,
    expected_decision: str,
    mock_slo_statuses: list[str],
    mock_error_budget_burn_rates: list[float],
    dry_run: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = str(deployment_profile).strip().lower() or "production"

    policy = _read_json_file(policy_file)
    runbook_text = runbook_file.read_text(encoding="utf-8")

    stages_raw = policy.get("stages") if isinstance(policy.get("stages"), list) else []
    _expect(stages_raw, "Policy requires non-empty 'stages'.")
    stages: list[dict[str, Any]] = []
    last_percent = 0
    for index, stage in enumerate(stages_raw):
        _expect(isinstance(stage, dict), f"Stage #{index + 1} is not an object.")
        name = str(stage.get("name") or "").strip()
        traffic_percent = int(stage.get("traffic_percent") or 0)
        min_samples = int(stage.get("min_samples") or 1)
        _expect(bool(name), f"Stage #{index + 1} missing 'name'.")
        _expect(traffic_percent > 0 and traffic_percent <= 100, f"Stage {name!r} has invalid traffic_percent={traffic_percent}.")
        _expect(traffic_percent > last_percent, f"Stage {name!r} traffic_percent must be strictly increasing.")
        _expect(min_samples > 0, f"Stage {name!r} has invalid min_samples={min_samples}.")
        last_percent = traffic_percent
        stages.append({"name": name, "traffic_percent": traffic_percent, "min_samples": min_samples})

    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    halt_statuses_raw = thresholds.get("halt_statuses") if isinstance(thresholds.get("halt_statuses"), list) else ["critical"]
    halt_statuses = {str(item).strip().lower() for item in halt_statuses_raw if str(item).strip()}
    _expect(bool(halt_statuses), "Policy threshold 'halt_statuses' must not be empty.")
    halt_on_non_ok = bool(thresholds.get("halt_on_non_ok", True))
    max_burn_rate = float(thresholds.get("max_error_budget_burn_rate_percent", 2.0))
    _expect(max_burn_rate >= 0.0, "Policy threshold 'max_error_budget_burn_rate_percent' must be >= 0.")

    runbook_sections = policy.get("runbook_sections") if isinstance(policy.get("runbook_sections"), dict) else {}
    runbook_halt = str(runbook_sections.get("halt") or "").strip()
    runbook_promote = str(runbook_sections.get("promotion") or "").strip()

    evaluations = _build_stage_evaluations(
        stages=stages,
        statuses=mock_slo_statuses,
        burn_rates=mock_error_budget_burn_rates,
        halt_statuses=halt_statuses,
        halt_on_non_ok=halt_on_non_ok,
        max_burn_rate=max_burn_rate,
    )

    halt_stage = next((item for item in evaluations if bool(item.get("halt"))), None)
    decision = "halt" if halt_stage is not None else "promote"

    _expect(
        str(expected_decision).strip().lower() in {"auto", "halt", "promote"},
        f"Invalid expected decision: {expected_decision!r}",
    )
    effective_expected_decision = str(expected_decision).strip().lower()

    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace / f"canary-guardrails-check-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    pre_state: dict[str, Any] | None = None
    post_state: dict[str, Any] | None = None
    freeze_state: dict[str, Any] | None = None
    promotion_probe_output = ""
    promotion_blocked = False

    try:
        _rings_unfreeze(
            manifest_path,
            ring="stable",
            reason="Reset freeze before canary guardrail evaluation.",
        )
        _rings_promote(manifest_path, ring="stable", version=stable_baseline_version)
        _rings_promote(manifest_path, ring="canary", version=canary_candidate_version)
        pre_state = _rings_show(manifest_path, ring="stable")

        if decision == "halt":
            halt_reason = (
                f"Canary guardrail halt at stage {halt_stage['stage_name']} "
                f"status={halt_stage['status']} burn={halt_stage['error_budget_burn_rate_percent']}"
            )
            if not dry_run:
                _rings_freeze(manifest_path, ring="stable", reason=halt_reason)
                freeze_state = _rings_show(manifest_path, ring="stable")
                promotion_probe = _rings_promote(
                    manifest_path,
                    ring="stable",
                    version=canary_candidate_version,
                    expect_success=False,
                )
                promotion_probe_output = "\n".join(
                    part for part in [promotion_probe.stdout.strip(), promotion_probe.stderr.strip()] if part
                )
                promotion_blocked = (
                    promotion_probe.returncode != 0
                    and "release freeze is active" in promotion_probe_output.lower()
                )
        else:
            if not dry_run:
                _rings_unfreeze(
                    manifest_path,
                    ring="stable",
                    reason="Canary guardrail allow promotion path.",
                )
                _rings_promote(manifest_path, ring="stable", version=canary_candidate_version)

        post_state = _rings_show(manifest_path, ring="stable")

        stable_ring_after = dict((post_state.get("rings") or {}).get("stable") or {})
        canary_ring_after = dict((post_state.get("rings") or {}).get("canary") or {})
        freeze_after = dict(post_state.get("release_freeze") or {})

        criteria: list[dict[str, Any]] = []

        def add(name: str, passed: bool, details: str) -> None:
            criteria.append({"name": name, "passed": bool(passed), "details": details})

        if profile == "production":
            add("policy_stages_valid", len(stages) > 0, f"stage_count={len(stages)}")
            add(
                "runbook_halt_section_present",
                bool(runbook_halt) and runbook_halt in runbook_text,
                f"runbook_halt_section={runbook_halt!r}",
            )
            add(
                "runbook_promotion_section_present",
                bool(runbook_promote) and runbook_promote in runbook_text,
                f"runbook_promotion_section={runbook_promote!r}",
            )
            add(
                "canary_seeded_with_candidate",
                str(canary_ring_after.get("version") or "") == canary_candidate_version,
                (
                    f"canary_version={canary_ring_after.get('version')!r}, "
                    f"expected={canary_candidate_version!r}"
                ),
            )

            if effective_expected_decision != "auto":
                add(
                    "decision_matches_expected",
                    decision == effective_expected_decision,
                    f"decision={decision!r}, expected={effective_expected_decision!r}",
                )

            if decision == "halt":
                add(
                    "halt_stage_detected",
                    halt_stage is not None,
                    f"halt_stage={halt_stage['stage_name'] if halt_stage else None}",
                )
                if dry_run:
                    add("halt_dry_run_mode", True, "dry_run=true")
                else:
                    add(
                        "release_freeze_active",
                        bool(freeze_after.get("active")),
                        f"freeze_active={freeze_after.get('active')!r}",
                    )
                    add(
                        "stable_not_promoted_when_halted",
                        str(stable_ring_after.get("version") or "") == stable_baseline_version,
                        (
                            f"stable_version={stable_ring_after.get('version')!r}, "
                            f"baseline={stable_baseline_version!r}"
                        ),
                    )
                    add(
                        "promotion_blocked_by_freeze",
                        bool(promotion_blocked),
                        f"promotion_probe_output={promotion_probe_output!r}",
                    )
            else:
                if dry_run:
                    add("promote_dry_run_mode", True, "dry_run=true")
                else:
                    add(
                        "stable_promoted_to_candidate",
                        str(stable_ring_after.get("version") or "") == canary_candidate_version,
                        (
                            f"stable_version={stable_ring_after.get('version')!r}, "
                            f"candidate={canary_candidate_version!r}"
                        ),
                    )
                    add(
                        "release_freeze_not_active",
                        not bool(freeze_after.get("active")),
                        f"freeze_active={freeze_after.get('active')!r}",
                    )
        else:
            add("non_production_profile", True, f"deployment_profile={profile!r} (hard requirements skipped)")

        failed = [item for item in criteria if item["passed"] is False]
        success = len(failed) == 0

        report = {
            "label": label,
            "success": bool(success),
            "config": {
                "deployment_profile": profile,
                "workspace": str(workspace),
                "manifest_path": str(manifest_path),
                "policy_file": str(policy_file),
                "runbook_file": str(runbook_file),
                "stable_baseline_version": stable_baseline_version,
                "canary_candidate_version": canary_candidate_version,
                "expected_decision": effective_expected_decision,
                "dry_run": bool(dry_run),
            },
            "policy": policy,
            "stage_evaluations": evaluations,
            "decision": {
                "result": decision,
                "halt_stage": halt_stage,
            },
            "rings": {
                "pre_state": pre_state,
                "freeze_state": freeze_state,
                "post_state": post_state,
                "promotion_probe_output": promotion_probe_output or None,
                "promotion_blocked": promotion_blocked,
            },
            "metrics": {
                "criteria_total": len(criteria),
                "criteria_passed": len(criteria) - len(failed),
                "criteria_failed": len(failed),
                "stage_count": len(stages),
                "evaluated_stage_count": len(evaluations),
                "halt_detected": halt_stage is not None,
            },
            "criteria": criteria,
            "failed_criteria": failed,
            "paths": {
                "run_dir": str(run_dir),
            },
            "generated_at_utc": _utc_now(),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return report
    finally:
        if not dry_run and run_dir.exists():
            # Keep explicit run artifacts only when debugging; default run cleans up workspace noise.
            shutil.rmtree(run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate canary promotion guardrails with staged traffic signals and enforce automatic halt behavior."
        )
    )
    parser.add_argument("--label", default="canary-guardrails-check")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "canary-guardrails"))
    parser.add_argument("--manifest-path", default=str(PROJECT_ROOT / ".tmp" / "canary-guardrails" / "desktop-rings.json"))
    parser.add_argument("--policy-file", default="docs/canary-guardrails-policy.json")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--stable-baseline-version", default="0.0.1")
    parser.add_argument("--canary-candidate-version", default="0.0.2")
    parser.add_argument("--expected-decision", default="auto")
    parser.add_argument("--mock-slo-statuses", default="ok,ok,critical,critical")
    parser.add_argument("--mock-error-budget-burn-rates", default="0.5,0.8,2.5,2.5")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]

    workspace = Path(str(args.workspace)).expanduser()
    if not workspace.is_absolute():
        workspace = (project_root / workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(str(args.manifest_path)).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = (project_root / manifest_path).resolve()

    policy_file = Path(str(args.policy_file)).expanduser()
    if not policy_file.is_absolute():
        policy_file = (project_root / policy_file).resolve()

    runbook_file = Path(str(args.runbook_file)).expanduser()
    if not runbook_file.is_absolute():
        runbook_file = (project_root / runbook_file).resolve()

    statuses = [item.strip() for item in str(args.mock_slo_statuses).split(",") if item.strip()]
    burn_rates = [float(item.strip()) for item in str(args.mock_error_budget_burn_rates).split(",") if item.strip()]

    try:
        report = run_check(
            label=str(args.label),
            deployment_profile=str(args.deployment_profile),
            workspace=workspace,
            manifest_path=manifest_path,
            policy_file=policy_file,
            runbook_file=runbook_file,
            stable_baseline_version=str(args.stable_baseline_version).strip(),
            canary_candidate_version=str(args.canary_candidate_version).strip(),
            expected_decision=str(args.expected_decision).strip().lower(),
            mock_slo_statuses=statuses,
            mock_error_budget_burn_rates=burn_rates,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        print(f"[canary-guardrails-check] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[canary-guardrails-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
