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


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _extract_json_from_text(raw: str) -> dict[str, Any] | None:
    candidate = str(raw or "").strip()
    if not candidate:
        return None
    lines = [line.strip() for line in candidate.splitlines() if line.strip()]
    if lines:
        try:
            payload = json.loads(lines[-1])
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(candidate[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
    return None


def _build_default_plan(
    *,
    profile: str,
    python_exe: str,
    run_dir: Path,
    runbook_file: Path,
    rto_rpo_policy_file: Path,
) -> list[dict[str, Any]]:
    if profile == "release-gate":
        return [
            {
                "name": "snapshot_restore_crash_consistency",
                "command": [
                    python_exe,
                    str(PROJECT_ROOT / "scripts" / "snapshot-restore-crash-consistency-drill.py"),
                    "--workspace",
                    str((run_dir / "snapshot-restore").resolve()),
                    "--label",
                    "disaster-recovery-pack-release-gate",
                    "--seed-rows",
                    "80",
                    "--payload-bytes",
                    "128",
                ],
            },
            {
                "name": "multi_db_atomic_switch",
                "command": [
                    python_exe,
                    str(PROJECT_ROOT / "scripts" / "multi-db-atomic-switch-drill.py"),
                    "--workspace",
                    str((run_dir / "multi-db-atomic-switch").resolve()),
                    "--label",
                    "disaster-recovery-pack-release-gate",
                    "--seed-rows",
                    "80",
                    "--payload-bytes",
                    "128",
                ],
            },
            {
                "name": "rto_rpo_assertion",
                "command": [
                    python_exe,
                    str(PROJECT_ROOT / "scripts" / "rto-rpo-assertion-suite.py"),
                    "--workspace",
                    str((run_dir / "rto-rpo-assertion").resolve()),
                    "--label",
                    "disaster-recovery-pack-release-gate",
                    "--deployment-profile",
                    "production",
                    "--policy-file",
                    str(rto_rpo_policy_file.resolve()),
                    "--runbook-file",
                    str(runbook_file.resolve()),
                    "--seed-rows",
                    "48",
                    "--tail-write-rows",
                    "12",
                    "--max-rto-seconds",
                    "20",
                    "--max-rpo-rows-lost",
                    "96",
                    "--output-file",
                    str((run_dir / "rto-rpo-assertion-report.json").resolve()),
                ],
            },
        ]

    _expect(profile == "scheduled", f"Unsupported profile {profile!r}.")
    return [
        {
            "name": "backup_restore",
            "command": [
                python_exe,
                str(PROJECT_ROOT / "scripts" / "backup-restore-drill.py"),
                "--workspace",
                str((run_dir / "backup-restore").resolve()),
                "--label",
                "disaster-recovery-pack-scheduled",
            ],
        },
        {
            "name": "backup_restore_stress",
            "command": [
                python_exe,
                str(PROJECT_ROOT / "scripts" / "backup-restore-stress-drill.py"),
                "--workspace",
                str((run_dir / "backup-restore-stress").resolve()),
                "--label",
                "disaster-recovery-pack-scheduled",
                "--rounds",
                "3",
                "--goals-per-round",
                "120",
                "--tasks-per-goal",
                "2",
                "--workflow-runs-per-round",
                "24",
            ],
        },
        {
            "name": "snapshot_restore_crash_consistency",
            "command": [
                python_exe,
                str(PROJECT_ROOT / "scripts" / "snapshot-restore-crash-consistency-drill.py"),
                "--workspace",
                str((run_dir / "snapshot-restore").resolve()),
                "--label",
                "disaster-recovery-pack-scheduled",
                "--seed-rows",
                "96",
                "--payload-bytes",
                "128",
            ],
        },
        {
            "name": "multi_db_atomic_switch",
            "command": [
                python_exe,
                str(PROJECT_ROOT / "scripts" / "multi-db-atomic-switch-drill.py"),
                "--workspace",
                str((run_dir / "multi-db-atomic-switch").resolve()),
                "--label",
                "disaster-recovery-pack-scheduled",
                "--seed-rows",
                "96",
                "--payload-bytes",
                "128",
            ],
        },
        {
            "name": "rto_rpo_assertion",
            "command": [
                python_exe,
                str(PROJECT_ROOT / "scripts" / "rto-rpo-assertion-suite.py"),
                "--workspace",
                str((run_dir / "rto-rpo-assertion").resolve()),
                "--label",
                "disaster-recovery-pack-scheduled",
                "--deployment-profile",
                "production",
                "--policy-file",
                str(rto_rpo_policy_file.resolve()),
                "--runbook-file",
                str(runbook_file.resolve()),
                "--seed-rows",
                "48",
                "--tail-write-rows",
                "12",
                "--max-rto-seconds",
                "20",
                "--max-rpo-rows-lost",
                "96",
                "--output-file",
                str((run_dir / "rto-rpo-assertion-report.json").resolve()),
            ],
        },
    ]


def _run_drill(name: str, command: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    duration_seconds = round(float(time.perf_counter() - started), 3)

    stdout_text = str(completed.stdout or "")
    stderr_text = str(completed.stderr or "")
    payload = _extract_json_from_text(stdout_text)
    if payload is None and completed.returncode != 0:
        payload = _extract_json_from_text(stderr_text)

    payload_success = (
        bool(payload.get("success")) if isinstance(payload, dict) and isinstance(payload.get("success"), bool) else None
    )
    success = completed.returncode == 0 and payload_success is True
    error_summary = ""
    if completed.returncode != 0:
        error_summary = (stderr_text.strip() or stdout_text.strip())[:400]
    elif payload_success is not True:
        error_summary = "Command succeeded but payload.success != true."

    return {
        "name": name,
        "command": command,
        "returncode": int(completed.returncode),
        "duration_seconds": duration_seconds,
        "success": bool(success),
        "payload_success": payload_success,
        "payload": payload if isinstance(payload, dict) else None,
        "stdout_excerpt": stdout_text.strip()[:400],
        "stderr_excerpt": stderr_text.strip()[:400],
        "error_summary": error_summary or None,
    }


def _load_mock_results(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), "Mock drill results file must contain a JSON object.")
    drills = payload.get("drills")
    _expect(isinstance(drills, list), "Mock drill results file must define list key 'drills'.")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(drills):
        _expect(isinstance(item, dict), f"Mock drill result at index {index} must be an object.")
        name = str(item.get("name") or "").strip()
        _expect(bool(name), f"Mock drill result at index {index} is missing 'name'.")
        success_value = item.get("success")
        _expect(
            isinstance(success_value, bool),
            f"Mock drill result '{name}' must provide boolean 'success'.",
        )
        payload_value = item.get("payload")
        normalized.append(
            {
                "name": name,
                "command": ["<mock>"],
                "returncode": 0 if success_value else 1,
                "duration_seconds": round(float(item.get("duration_seconds", 0.01)), 3),
                "success": bool(success_value),
                "payload_success": bool(success_value),
                "payload": payload_value if isinstance(payload_value, dict) else {"success": bool(success_value)},
                "stdout_excerpt": "<mock>",
                "stderr_excerpt": "",
                "error_summary": None if success_value else "Mock drill marked as failed.",
            }
        )
    return normalized


def run_pack(
    *,
    label: str,
    profile: str,
    python_exe: str,
    workspace_root: Path,
    runbook_file: Path,
    rto_rpo_policy_file: Path,
    drill_filter: list[str],
    mock_drill_results_file: Path | None,
    max_failed_drills: int,
    max_total_duration_seconds: int,
    output_file: Path,
    evidence_dir: Path,
    keep_artifacts: bool,
    allow_failure: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"disaster-recovery-rehearsal-pack-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    try:
        if mock_drill_results_file is not None:
            drill_results = _load_mock_results(mock_drill_results_file)
            effective_profile = "mock"
        else:
            plan = _build_default_plan(
                profile=profile,
                python_exe=python_exe,
                run_dir=run_dir,
                runbook_file=runbook_file,
                rto_rpo_policy_file=rto_rpo_policy_file,
            )
            available_names = [str(item["name"]) for item in plan]
            if drill_filter:
                unknown = [name for name in drill_filter if name not in available_names]
                _expect(not unknown, f"Unknown drill-filter item(s): {unknown}")
                selected = [item for item in plan if str(item["name"]) in drill_filter]
            else:
                selected = plan
            _expect(selected, "No drills selected for disaster recovery rehearsal pack.")
            drill_results = [_run_drill(str(item["name"]), list(item["command"])) for item in selected]
            effective_profile = profile

        copied_evidence_files: list[str] = []
        for drill in drill_results:
            evidence_path = evidence_dir / f"{drill['name']}-report.json"
            evidence_payload = drill["payload"]
            if not isinstance(evidence_payload, dict):
                evidence_payload = {
                    "success": False,
                    "error": str(drill.get("error_summary") or "missing payload"),
                    "name": str(drill["name"]),
                }
            evidence_path.write_text(
                json.dumps(evidence_payload, ensure_ascii=True, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            copied_evidence_files.append(str(evidence_path.resolve()))

        failed_drills = [item for item in drill_results if not bool(item.get("success"))]
        total_duration_seconds = round(sum(float(item.get("duration_seconds") or 0.0) for item in drill_results), 3)
        duration_budget_exceeded = total_duration_seconds > float(max_total_duration_seconds)

        success = len(failed_drills) <= int(max_failed_drills) and not duration_budget_exceeded
        report = {
            "label": label,
            "success": bool(success),
            "profile": effective_profile,
            "config": {
                "python_exe": python_exe,
                "workspace_root": str(workspace_root),
                "runbook_file": str(runbook_file),
                "rto_rpo_policy_file": str(rto_rpo_policy_file),
                "drill_filter": drill_filter,
                "mock_drill_results_file": str(mock_drill_results_file) if mock_drill_results_file else None,
                "max_failed_drills": int(max_failed_drills),
                "max_total_duration_seconds": int(max_total_duration_seconds),
                "keep_artifacts": bool(keep_artifacts),
                "allow_failure": bool(allow_failure),
            },
            "metrics": {
                "drills_total": len(drill_results),
                "drills_passed": len(drill_results) - len(failed_drills),
                "drills_failed": len(failed_drills),
                "total_duration_seconds": total_duration_seconds,
                "duration_budget_exceeded": bool(duration_budget_exceeded),
            },
            "decision": {
                "release_blocked": not bool(success),
                "recommended_action": "block_release" if not success else "proceed",
                "runbook_path": "docs/production-runbook.md#339-disaster-recovery-rehearsal-pack",
            },
            "drills": drill_results,
            "failed_drills": failed_drills,
            "paths": {
                "run_dir": str(run_dir),
                "output_file": str(output_file),
                "evidence_dir": str(evidence_dir),
                "evidence_files": copied_evidence_files,
            },
            "generated_at_utc": _utc_now(),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        output_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

        if not success and not allow_failure:
            raise RuntimeError(f"Disaster recovery rehearsal pack failed: {json.dumps(report, sort_keys=True)}")
        return report
    finally:
        if not keep_artifacts and run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a disaster-recovery rehearsal pack (restore/snapshot/switch/RTO-RPO) and emit "
            "a consolidated evidence report for release blocking and scheduled operations."
        )
    )
    parser.add_argument("--label", default="disaster-recovery-rehearsal-pack")
    parser.add_argument("--profile", choices=["release-gate", "scheduled"], default="scheduled")
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "disaster-recovery-rehearsal-pack"))
    parser.add_argument("--runbook-file", default=str(PROJECT_ROOT / "docs" / "production-runbook.md"))
    parser.add_argument("--rto-rpo-policy-file", default=str(PROJECT_ROOT / "docs" / "rto-rpo-assertion-policy.json"))
    parser.add_argument("--drill-filter", default="")
    parser.add_argument("--mock-drill-results-file", default="")
    parser.add_argument("--max-failed-drills", type=int, default=0)
    parser.add_argument("--max-total-duration-seconds", type=int, default=2400)
    parser.add_argument(
        "--output-file",
        default=str(PROJECT_ROOT / "artifacts" / "disaster-recovery-rehearsal-pack-report.json"),
    )
    parser.add_argument(
        "--evidence-dir",
        default=str(PROJECT_ROOT / "artifacts" / "disaster-recovery-rehearsal-pack-evidence"),
    )
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    if int(args.max_failed_drills) < 0:
        print("[disaster-recovery-rehearsal-pack] ERROR: --max-failed-drills must be >= 0.", file=sys.stderr)
        return 2
    if int(args.max_total_duration_seconds) <= 0:
        print("[disaster-recovery-rehearsal-pack] ERROR: --max-total-duration-seconds must be > 0.", file=sys.stderr)
        return 2

    drill_filter = _parse_csv_list(args.drill_filter)
    mock_results_file = None
    if str(args.mock_drill_results_file).strip():
        mock_results_file = _resolve_path(PROJECT_ROOT, args.mock_drill_results_file)
        if not mock_results_file.exists():
            print(
                f"[disaster-recovery-rehearsal-pack] ERROR: mock results file not found: {mock_results_file}",
                file=sys.stderr,
            )
            return 2

    try:
        report = run_pack(
            label=str(args.label),
            profile=str(args.profile),
            python_exe=str(args.python_exe),
            workspace_root=_resolve_path(PROJECT_ROOT, args.workspace),
            runbook_file=_resolve_path(PROJECT_ROOT, args.runbook_file),
            rto_rpo_policy_file=_resolve_path(PROJECT_ROOT, args.rto_rpo_policy_file),
            drill_filter=drill_filter,
            mock_drill_results_file=mock_results_file,
            max_failed_drills=int(args.max_failed_drills),
            max_total_duration_seconds=int(args.max_total_duration_seconds),
            output_file=_resolve_path(PROJECT_ROOT, args.output_file),
            evidence_dir=_resolve_path(PROJECT_ROOT, args.evidence_dir),
            keep_artifacts=bool(args.keep_artifacts),
            allow_failure=bool(args.allow_failure),
        )
    except Exception as exc:
        print(f"[disaster-recovery-rehearsal-pack] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
