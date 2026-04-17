from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def _is_iso_utc(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    try:
        datetime.strptime(candidate, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def run_check(
    *,
    label: str,
    step_timings_file: Path,
    required_label: str,
    required_keys: list[str],
    output_file: Path,
    allow_failed_steps: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_keys, "At least one required step key is required.")
    payload = _read_json_object(step_timings_file)

    observed_label = str(payload.get("label") or "")
    label_matches = (not required_label) or (observed_label == required_label)
    steps_raw = payload.get("steps")
    _expect(isinstance(steps_raw, list) and steps_raw, f"Step timings file requires non-empty 'steps': {step_timings_file}")

    schema_failed_steps: list[dict[str, Any]] = []
    failed_step_entries: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []

    for index, raw in enumerate(steps_raw, start=1):
        if not isinstance(raw, dict):
            schema_failed_steps.append({"index": index, "error": "step entry is not an object"})
            continue
        missing_keys = [key for key in required_keys if key not in raw]
        if missing_keys:
            schema_failed_steps.append({"index": index, "missing_keys": missing_keys})
            continue

        name = str(raw.get("name") or "")
        success_value = raw.get("success")
        duration_seconds_value = raw.get("duration_seconds")
        completed_at_utc = str(raw.get("completed_at_utc") or "")

        step_errors: list[str] = []
        if not name.strip():
            step_errors.append("name is empty")
        if not isinstance(success_value, bool):
            step_errors.append("success is not bool")
        try:
            duration_seconds = float(duration_seconds_value)
        except (TypeError, ValueError):
            duration_seconds = -1.0
        if duration_seconds < 0.0:
            step_errors.append("duration_seconds is negative or non-numeric")
        if not _is_iso_utc(completed_at_utc):
            step_errors.append("completed_at_utc is not valid ISO UTC")

        if step_errors:
            schema_failed_steps.append(
                {
                    "index": index,
                    "name": name,
                    "errors": step_errors,
                }
            )
            continue

        step = {
            "index": index,
            "name": name,
            "duration_seconds": round(duration_seconds, 3),
            "success": bool(success_value),
            "completed_at_utc": completed_at_utc,
        }
        steps.append(step)
        if not bool(success_value):
            failed_step_entries.append(step)

    success = (
        label_matches
        and not schema_failed_steps
        and (allow_failed_steps or not failed_step_entries)
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "step_timings_file": str(step_timings_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_label": required_label,
            "required_keys": required_keys,
            "allow_failed_steps": bool(allow_failed_steps),
        },
        "metrics": {
            "steps_total": len(steps_raw),
            "steps_schema_valid": len(steps),
            "schema_failed_steps": len(schema_failed_steps),
            "failed_step_entries": len(failed_step_entries),
            "label_mismatch_reports": 0 if label_matches else 1,
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "checks": {
            "required_label": required_label,
            "observed_label": observed_label,
            "label_matches_required": bool(label_matches),
            "schema_failed_steps": schema_failed_steps,
            "failed_step_entries": failed_step_entries,
        },
        "steps": steps,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if not success:
        raise RuntimeError(f"Release-gate step timing schema check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate release-gate step timing report schema and step-success contract."
        )
    )
    parser.add_argument("--label", default="release-gate-step-timing-schema-check")
    parser.add_argument("--project-root")
    parser.add_argument("--step-timings-file", default="artifacts/release-gate-step-timings-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--required-keys", default="name,duration_seconds,success,completed_at_utc")
    parser.add_argument("--output-file", default="artifacts/release-gate-step-timing-schema-release-gate.json")
    parser.add_argument("--allow-failed-steps", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    step_timings_file = _resolve_path(project_root, args.step_timings_file)
    output_file = _resolve_path(project_root, args.output_file)
    required_keys = _parse_csv_list(args.required_keys)
    if not required_keys:
        print("[release-gate-step-timing-schema-check] ERROR: --required-keys must not be empty.", file=sys.stderr)
        return 2

    try:
        report = run_check(
            label=str(args.label),
            step_timings_file=step_timings_file,
            required_label=str(args.required_label),
            required_keys=required_keys,
            output_file=output_file,
            allow_failed_steps=bool(args.allow_failed_steps),
        )
    except Exception as exc:
        print(f"[release-gate-step-timing-schema-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
