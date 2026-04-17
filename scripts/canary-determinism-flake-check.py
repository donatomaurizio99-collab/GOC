from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ProbeBuilder = Callable[[Path, str, Path], list[str]]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _safe_mode_probe_command(project_root: Path, label: str, output_file: Path) -> list[str]:
    return [
        sys.executable,
        str(project_root / "scripts" / "safe-mode-ux-degradation-check.py"),
        "--label",
        label,
        "--output-file",
        str(output_file),
    ]


def _a11y_probe_command(project_root: Path, label: str, output_file: Path) -> list[str]:
    return [
        sys.executable,
        str(project_root / "scripts" / "a11y-test-harness-check.py"),
        "--label",
        label,
        "--output-file",
        str(output_file),
    ]


PROBE_BUILDERS: dict[str, ProbeBuilder] = {
    "safe_mode_ux_degradation": _safe_mode_probe_command,
    "a11y_test_harness": _a11y_probe_command,
}


def _extract_json_from_stdout(stdout_text: str) -> dict[str, Any] | None:
    output_lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if not output_lines:
        return None
    try:
        payload = json.loads(output_lines[-1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _run_probe_once(
    *,
    probe_id: str,
    command: list[str],
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    payload = _extract_json_from_stdout(completed.stdout)
    if payload is None and output_file.exists():
        try:
            payload = _read_json_object(output_file)
        except Exception:
            payload = None
    if payload is None:
        payload = {}

    return {
        "probe_id": probe_id,
        "command": command,
        "return_code": int(completed.returncode),
        "duration_ms": int(duration_ms),
        "success": bool(completed.returncode == 0 and payload.get("success") is True),
        "label": str(payload.get("label") or ""),
        "stdout_excerpt": completed.stdout.strip()[:2000],
        "stderr_excerpt": completed.stderr.strip()[:2000],
    }


def _parse_iso_utc(text: str) -> datetime | None:
    candidate = str(text).strip()
    if not candidate:
        return None
    try:
        parsed = datetime.strptime(candidate, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _load_quarantine_entries(path: Path, now_utc: datetime) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = _read_json_object(path)
    entries_raw = payload.get("quarantined_probes")
    entries = entries_raw if isinstance(entries_raw, list) else []
    active: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        probe_id = str(entry.get("id") or "").strip()
        if not probe_id:
            continue
        expires_raw = str(entry.get("expires_utc") or "").strip()
        expires_at = _parse_iso_utc(expires_raw) if expires_raw else None
        if expires_at is not None and expires_at < now_utc:
            continue
        active[probe_id] = {
            "id": probe_id,
            "reason": str(entry.get("reason") or "").strip(),
            "expires_utc": expires_raw or None,
        }
    return active


def _normalize_mock_runs(payload: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    probes_raw = payload.get("probes")
    probes = probes_raw if isinstance(probes_raw, list) else []
    runs_by_probe: dict[str, list[dict[str, Any]]] = {}
    unknown_probe_ids: list[str] = []
    for entry in probes:
        if not isinstance(entry, dict):
            continue
        probe_id = str(entry.get("id") or "").strip()
        if not probe_id:
            continue
        if probe_id not in PROBE_BUILDERS:
            unknown_probe_ids.append(probe_id)
        runs_raw = entry.get("runs")
        runs_list = runs_raw if isinstance(runs_raw, list) else []
        normalized_runs: list[dict[str, Any]] = []
        for run in runs_list:
            if not isinstance(run, dict):
                continue
            duration_ms_raw = run.get("duration_ms", 0)
            try:
                duration_ms = int(float(duration_ms_raw))
            except (TypeError, ValueError):
                duration_ms = 0
            normalized_runs.append(
                {
                    "probe_id": probe_id,
                    "command": ["mock"],
                    "return_code": 0 if bool(run.get("success") is True) else 1,
                    "duration_ms": max(0, duration_ms),
                    "success": bool(run.get("success") is True),
                    "label": str(run.get("label") or ""),
                    "stdout_excerpt": "mock",
                    "stderr_excerpt": "",
                }
            )
        runs_by_probe[probe_id] = normalized_runs
    return runs_by_probe, sorted(set(unknown_probe_ids))


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    quarantine_file: Path,
    runbook_file: Path,
    workspace: Path,
    required_label: str,
    probe_repeats_override: int,
    mock_results_file: Path | None,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    policy = _read_json_object(policy_file)
    runbook_text = runbook_file.read_text(encoding="utf-8")
    now_utc = datetime.now(timezone.utc)
    quarantine_entries = _load_quarantine_entries(quarantine_file, now_utc)

    thresholds = policy.get("thresholds")
    _expect(isinstance(thresholds, dict), f"Policy file must contain object 'thresholds': {policy_file}")
    default_probe_repeats = int(thresholds.get("default_probe_repeats", 2))
    min_success_rate_percent = float(thresholds.get("min_success_rate_percent", 100.0))
    max_flaky_probes = int(thresholds.get("max_flaky_probes", 0))
    _expect(default_probe_repeats > 0, "Policy threshold default_probe_repeats must be > 0.")
    _expect(max_flaky_probes >= 0, "Policy threshold max_flaky_probes must be >= 0.")
    _expect(
        min_success_rate_percent >= 0.0 and min_success_rate_percent <= 100.0,
        "Policy threshold min_success_rate_percent must be between 0 and 100.",
    )

    effective_repeats = int(probe_repeats_override) if int(probe_repeats_override) > 0 else default_probe_repeats
    _expect(effective_repeats > 0, "Effective probe repeat count must be > 0.")

    required_runbook_section = str(policy.get("required_runbook_section") or "").strip()
    _expect(required_runbook_section, f"Policy file requires non-empty required_runbook_section: {policy_file}")
    runbook_section_present = required_runbook_section in runbook_text

    probes_raw = policy.get("probes")
    _expect(isinstance(probes_raw, list) and len(probes_raw) > 0, f"Policy file requires non-empty 'probes': {policy_file}")

    mock_runs_by_probe: dict[str, list[dict[str, Any]]] = {}
    unknown_mock_probe_ids: list[str] = []
    if mock_results_file is not None:
        mock_payload = _read_json_object(mock_results_file)
        mock_runs_by_probe, unknown_mock_probe_ids = _normalize_mock_runs(mock_payload)

    workspace.mkdir(parents=True, exist_ok=True)

    probe_results: list[dict[str, Any]] = []
    criteria: list[dict[str, Any]] = []
    blocking_flaky_probe_ids: list[str] = []

    for probe_config_raw in probes_raw:
        _expect(isinstance(probe_config_raw, dict), f"Probe entry must be an object: {probe_config_raw!r}")
        probe_id = str(probe_config_raw.get("id") or "").strip()
        _expect(probe_id, f"Probe entry missing id: {probe_config_raw!r}")
        _expect(probe_id in PROBE_BUILDERS, f"Unsupported probe id in policy: {probe_id}")

        max_duration_cv_percent = float(probe_config_raw.get("max_duration_cv_percent", 40.0))
        max_failed_runs = int(probe_config_raw.get("max_failed_runs", 0))
        _expect(max_duration_cv_percent >= 0.0, f"Probe {probe_id}: max_duration_cv_percent must be >= 0.")
        _expect(max_failed_runs >= 0, f"Probe {probe_id}: max_failed_runs must be >= 0.")

        runs: list[dict[str, Any]] = []
        if mock_results_file is not None:
            runs = list(mock_runs_by_probe.get(probe_id) or [])
        else:
            builder = PROBE_BUILDERS[probe_id]
            for run_index in range(effective_repeats):
                run_output_file = workspace / f"{probe_id}-run-{run_index + 1}.json"
                command = builder(project_root, required_label, run_output_file)
                runs.append(_run_probe_once(probe_id=probe_id, command=command, output_file=run_output_file))

        observed_runs = len(runs)
        success_count = sum(1 for item in runs if bool(item.get("success")))
        failed_runs = observed_runs - success_count
        label_mismatch_runs = (
            sum(1 for item in runs if str(item.get("label") or "") != required_label)
            if required_label
            else 0
        )
        success_rate_percent = (100.0 * float(success_count) / float(observed_runs)) if observed_runs > 0 else 0.0

        durations = [float(item.get("duration_ms") or 0.0) for item in runs]
        mean_duration_ms = statistics.fmean(durations) if durations else 0.0
        cv_percent = 0.0
        if len(durations) >= 2 and mean_duration_ms > 0.0:
            cv_percent = (statistics.pstdev(durations) / mean_duration_ms) * 100.0

        flaky_reasons: list[str] = []
        if observed_runs < effective_repeats:
            flaky_reasons.append(f"insufficient_observations(observed={observed_runs}, required={effective_repeats})")
        if failed_runs > max_failed_runs:
            flaky_reasons.append(f"failed_runs({failed_runs}>{max_failed_runs})")
        if success_rate_percent < min_success_rate_percent:
            flaky_reasons.append(
                f"success_rate_percent({round(success_rate_percent, 3)}<{round(min_success_rate_percent, 3)})"
            )
        if required_label and label_mismatch_runs > 0:
            flaky_reasons.append(f"label_mismatch_runs({label_mismatch_runs})")
        if len(durations) >= 2 and cv_percent > max_duration_cv_percent:
            flaky_reasons.append(
                f"duration_cv_percent({round(cv_percent, 3)}>{round(max_duration_cv_percent, 3)})"
            )

        quarantine_entry = quarantine_entries.get(probe_id)
        quarantined = bool(quarantine_entry) and len(flaky_reasons) > 0
        blocking = len(flaky_reasons) > 0 and not quarantined
        if blocking:
            blocking_flaky_probe_ids.append(probe_id)

        probe_results.append(
            {
                "id": probe_id,
                "runs": runs,
                "config": {
                    "max_duration_cv_percent": max_duration_cv_percent,
                    "max_failed_runs": max_failed_runs,
                    "required_runs": effective_repeats,
                },
                "metrics": {
                    "observed_runs": observed_runs,
                    "success_count": success_count,
                    "failed_runs": failed_runs,
                    "label_mismatch_runs": label_mismatch_runs,
                    "success_rate_percent": round(success_rate_percent, 3),
                    "mean_duration_ms": round(mean_duration_ms, 3),
                    "duration_cv_percent": round(cv_percent, 3),
                },
                "flaky_reasons": flaky_reasons,
                "quarantined": quarantined,
                "quarantine": quarantine_entry,
                "blocking": blocking,
            }
        )

        criteria.append(
            {
                "name": f"probe.{probe_id}.observations",
                "passed": observed_runs >= effective_repeats,
                "details": f"observed={observed_runs}, required={effective_repeats}",
            }
        )
        criteria.append(
            {
                "name": f"probe.{probe_id}.flake_budget",
                "passed": not blocking,
                "details": f"blocking={blocking}, reasons={flaky_reasons}",
            }
        )

    criteria.append(
        {
            "name": "runbook_section_present",
            "passed": runbook_section_present,
            "details": f"required_runbook_section={required_runbook_section!r}",
        }
    )
    criteria.append(
        {
            "name": "unknown_mock_probe_ids",
            "passed": len(unknown_mock_probe_ids) == 0,
            "details": f"unknown_mock_probe_ids={unknown_mock_probe_ids}",
        }
    )
    criteria.append(
        {
            "name": "blocking_flaky_probe_budget",
            "passed": len(blocking_flaky_probe_ids) <= max_flaky_probes,
            "details": (
                f"blocking_flaky_probe_count={len(blocking_flaky_probe_ids)}, "
                f"max_flaky_probes={max_flaky_probes}"
            ),
        }
    )

    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "project_root": str(project_root),
            "policy_file": str(policy_file),
            "quarantine_file": str(quarantine_file),
            "runbook_file": str(runbook_file),
            "workspace": str(workspace),
            "required_label": required_label,
            "probe_repeats": int(effective_repeats),
            "mock_results_file": str(mock_results_file) if mock_results_file is not None else None,
        },
        "policy": {
            "required_runbook_section": required_runbook_section,
            "thresholds": thresholds,
            "probes": probes_raw,
        },
        "metrics": {
            "probe_count": len(probe_results),
            "blocking_flaky_probe_count": len(blocking_flaky_probe_ids),
            "max_flaky_probes": max_flaky_probes,
            "quarantined_probe_count": sum(1 for item in probe_results if bool(item.get("quarantined"))),
            "unknown_mock_probe_count": len(unknown_mock_probe_ids),
            "criteria_total": len(criteria),
            "criteria_failed": len(failed_criteria),
        },
        "checks": {
            "runbook_section_present": runbook_section_present,
            "blocking_flaky_probe_ids": blocking_flaky_probe_ids,
            "unknown_mock_probe_ids": unknown_mock_probe_ids,
            "active_quarantine_entries": sorted(quarantine_entries.keys()),
        },
        "probes": probe_results,
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Canary determinism flake check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run repeated canary contract probes and detect flaky/non-deterministic behavior with quarantine support."
        )
    )
    parser.add_argument("--label", default="canary-determinism-flake-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/canary-determinism-policy.json")
    parser.add_argument("--quarantine-file", default="docs/canary-determinism-quarantine.json")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--workspace", default=".tmp/canary-determinism-flake-check")
    parser.add_argument("--required-label", default="stability-canary")
    parser.add_argument("--probe-repeats", type=int, default=0)
    parser.add_argument("--mock-results-file")
    parser.add_argument("--output-file", default="artifacts/canary-determinism-flake-report.json")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    quarantine_file = _resolve_path(project_root, args.quarantine_file)
    runbook_file = _resolve_path(project_root, args.runbook_file)
    workspace = _resolve_path(project_root, args.workspace)
    output_file = _resolve_path(project_root, args.output_file)
    mock_results_file = _resolve_path(project_root, args.mock_results_file) if args.mock_results_file else None

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            quarantine_file=quarantine_file,
            runbook_file=runbook_file,
            workspace=workspace,
            required_label=str(args.required_label),
            probe_repeats_override=int(args.probe_repeats),
            mock_results_file=mock_results_file,
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[canary-determinism-flake-check] ERROR: {exc}", file=sys.stderr)
        return 1

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[canary-determinism-flake-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
