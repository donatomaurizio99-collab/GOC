from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_step_entry(entry: dict[str, Any]) -> dict[str, Any]:
    name = str(entry.get("name") or "").strip()
    duration_seconds = _to_float(entry.get("duration_seconds"), default=0.0)
    baseline_seconds = _to_float(entry.get("baseline_duration_seconds"), default=0.0)
    max_duration_seconds = _to_float(entry.get("max_duration_seconds"), default=0.0)
    max_regression_percent = entry.get("max_regression_percent")
    return {
        "name": name,
        "duration_seconds": max(0.0, duration_seconds),
        "baseline_duration_seconds": max(0.0, baseline_seconds),
        "max_duration_seconds": max(0.0, max_duration_seconds),
        "max_regression_percent": _to_float(max_regression_percent, default=0.0)
        if max_regression_percent is not None
        else None,
    }


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    step_timings_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_steps: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    policy = _read_json_object(policy_file)
    timings = _read_json_object(step_timings_file)

    policy_steps_raw = policy.get("steps")
    _expect(isinstance(policy_steps_raw, list) and policy_steps_raw, f"Policy file requires non-empty 'steps': {policy_file}")
    policy_steps: list[dict[str, Any]] = []
    for raw in policy_steps_raw:
        _expect(isinstance(raw, dict), f"Each policy step entry must be an object: {raw!r}")
        normalized = _normalize_step_entry(raw)
        _expect(normalized["name"], f"Policy step entry requires non-empty name: {raw!r}")
        _expect(
            normalized["max_duration_seconds"] > 0.0,
            f"Policy step {normalized['name']} requires max_duration_seconds > 0.",
        )
        _expect(
            normalized["baseline_duration_seconds"] > 0.0,
            f"Policy step {normalized['name']} requires baseline_duration_seconds > 0.",
        )
        policy_steps.append(normalized)

    max_total_duration_seconds = _to_float(policy.get("max_total_duration_seconds"), default=0.0)
    _expect(max_total_duration_seconds > 0.0, "Policy key max_total_duration_seconds must be > 0.")
    default_max_regression_percent = _to_float(policy.get("max_step_regression_percent"), default=0.0)
    _expect(default_max_regression_percent >= 0.0, "Policy key max_step_regression_percent must be >= 0.")
    trend_top_n = int(_to_float(policy.get("trend_top_n"), default=8.0))
    if trend_top_n <= 0:
        trend_top_n = 8

    step_entries_raw = timings.get("steps")
    _expect(isinstance(step_entries_raw, list) and step_entries_raw, f"Step timings file requires non-empty 'steps': {step_timings_file}")
    observed_label = str(timings.get("label") or "").strip()
    label_matches_required = (not required_label) or (observed_label == required_label)

    observed_steps: list[dict[str, Any]] = []
    observed_by_name: dict[str, dict[str, Any]] = {}
    for raw in step_entries_raw:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        duration_seconds = max(0.0, _to_float(raw.get("duration_seconds"), default=0.0))
        entry = {
            "name": name,
            "duration_seconds": duration_seconds,
            "success": bool(raw.get("success") is not False),
            "completed_at_utc": str(raw.get("completed_at_utc") or ""),
        }
        observed_steps.append(entry)
        observed_by_name[name] = entry

    _expect(observed_steps, f"No valid step entries found in timings file: {step_timings_file}")

    missing_required_steps: list[str] = []
    steps_over_budget: list[dict[str, Any]] = []
    regression_budget_exceeded: list[dict[str, Any]] = []
    trend_entries: list[dict[str, Any]] = []

    for policy_step in policy_steps:
        step_name = str(policy_step["name"])
        observed = observed_by_name.get(step_name)
        if observed is None:
            missing_required_steps.append(step_name)
            continue

        observed_seconds = float(observed["duration_seconds"])
        max_duration = float(policy_step["max_duration_seconds"])
        baseline_seconds = float(policy_step["baseline_duration_seconds"])
        max_regression = (
            float(policy_step["max_regression_percent"])
            if policy_step["max_regression_percent"] is not None
            else float(default_max_regression_percent)
        )

        if observed_seconds > max_duration:
            steps_over_budget.append(
                {
                    "name": step_name,
                    "duration_seconds": round(observed_seconds, 3),
                    "max_duration_seconds": round(max_duration, 3),
                }
            )

        regression_percent = ((observed_seconds - baseline_seconds) / baseline_seconds) * 100.0
        trend_entry = {
            "name": step_name,
            "duration_seconds": round(observed_seconds, 3),
            "baseline_duration_seconds": round(baseline_seconds, 3),
            "regression_percent": round(regression_percent, 3),
            "max_regression_percent": round(max_regression, 3),
        }
        trend_entries.append(trend_entry)
        if regression_percent > max_regression:
            regression_budget_exceeded.append(trend_entry)

    total_duration_seconds = sum(float(item["duration_seconds"]) for item in observed_steps)
    total_duration_over_budget = total_duration_seconds > max_total_duration_seconds

    sorted_by_regression = sorted(
        trend_entries,
        key=lambda item: float(item.get("regression_percent", 0.0)),
        reverse=True,
    )
    top_regressions = [item for item in sorted_by_regression if float(item.get("regression_percent", 0.0)) >= 0.0][
        :trend_top_n
    ]
    top_improvements = [
        item for item in sorted(sorted_by_regression, key=lambda row: float(row.get("regression_percent", 0.0))) if float(item.get("regression_percent", 0.0)) < 0.0
    ][:trend_top_n]

    success = (
        label_matches_required
        and (allow_missing_steps or not missing_required_steps)
        and not steps_over_budget
        and not regression_budget_exceeded
        and not total_duration_over_budget
    )

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "policy_file": str(policy_file),
            "step_timings_file": str(step_timings_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_label": required_label,
            "allow_missing_steps": bool(allow_missing_steps),
            "max_total_duration_seconds": max_total_duration_seconds,
            "max_step_regression_percent": default_max_regression_percent,
            "trend_top_n": trend_top_n,
            "policy_step_count": len(policy_steps),
        },
        "metrics": {
            "observed_step_count": len(observed_steps),
            "required_step_count": len(policy_steps),
            "missing_required_steps": len(missing_required_steps),
            "steps_over_budget": len(steps_over_budget),
            "regression_budget_exceeded": len(regression_budget_exceeded),
            "label_mismatch_reports": 0 if label_matches_required else 1,
            "total_duration_seconds": round(total_duration_seconds, 3),
            "max_total_duration_seconds": round(max_total_duration_seconds, 3),
            "total_duration_over_budget": bool(total_duration_over_budget),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "checks": {
            "required_label": required_label,
            "observed_label": observed_label,
            "label_matches_required": bool(label_matches_required),
            "missing_required_steps": missing_required_steps,
            "steps_over_budget": steps_over_budget,
            "regression_budget_exceeded": regression_budget_exceeded,
            "top_regressions": top_regressions,
            "top_improvements": top_improvements,
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if not success:
        raise RuntimeError(f"Release-gate performance budget check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate release-gate step durations against policy budgets and emit a step-level trend report."
        )
    )
    parser.add_argument("--label", default="release-gate-performance-budget-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-performance-budget-policy.json")
    parser.add_argument("--step-timings-file", default="artifacts/release-gate-step-timings-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-performance-budget-release-gate.json")
    parser.add_argument("--allow-missing-steps", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    step_timings_file = _resolve_path(project_root, args.step_timings_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            step_timings_file=step_timings_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_steps=bool(args.allow_missing_steps),
        )
    except Exception as exc:
        print(f"[release-gate-performance-budget-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
