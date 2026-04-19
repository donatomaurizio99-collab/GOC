from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVE_MTTR_TARGET_SECONDS = 300.0


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _resolve_active_target_seconds(
    *,
    active_policy_file: Path | None,
) -> tuple[float, str, str | None]:
    if active_policy_file is None:
        return float(DEFAULT_ACTIVE_MTTR_TARGET_SECONDS), "default", None
    if not active_policy_file.exists():
        return (
            float(DEFAULT_ACTIVE_MTTR_TARGET_SECONDS),
            "default",
            f"Active policy file not found: {active_policy_file}",
        )

    try:
        payload = _read_json_object(active_policy_file)
    except Exception as exc:  # noqa: BLE001
        return (
            float(DEFAULT_ACTIVE_MTTR_TARGET_SECONDS),
            "default",
            f"Failed to read active policy file: {exc}",
        )

    target_raw = payload.get("target_mttr_seconds")
    try:
        target_seconds = float(target_raw)
    except (TypeError, ValueError):
        return (
            float(DEFAULT_ACTIVE_MTTR_TARGET_SECONDS),
            "default",
            "Active policy target_mttr_seconds missing or non-numeric.",
        )
    if target_seconds <= 0.0:
        return (
            float(DEFAULT_ACTIVE_MTTR_TARGET_SECONDS),
            "default",
            "Active policy target_mttr_seconds must be > 0.",
        )
    return float(target_seconds), "policy_file", None


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _discover_report_files(project_root: Path, explicit_files: list[str], glob_pattern: str) -> list[Path]:
    discovered: list[Path] = []
    for value in explicit_files:
        candidate = _resolve_path(project_root, value)
        if candidate.exists() and candidate.is_file():
            discovered.append(candidate)
    if glob_pattern:
        discovered.extend(
            sorted(path.resolve() for path in project_root.glob(glob_pattern) if path.is_file())
        )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in discovered:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _percentile(values: list[float], percentile: float) -> float:
    _expect(values, "Cannot compute percentile for empty sample list.")
    sorted_values = sorted(float(item) for item in values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    p = max(0.0, min(100.0, float(percentile)))
    rank = (len(sorted_values) - 1) * p / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return float(sorted_values[lower])
    weight = rank - lower
    return float((sorted_values[lower] * (1.0 - weight)) + (sorted_values[upper] * weight))


def run_watchdog_mttr_calibration(
    *,
    label: str,
    project_root: Path,
    report_files: list[Path],
    min_samples: int,
    max_samples: int,
    percentile_target: float,
    headroom_percent: float,
    active_policy_file: Path | None,
    recommendation_delta_threshold_percent: float,
    output_file: Path,
    policy_output_file: Path,
    write_updates: bool,
    allow_insufficient_samples: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(min_samples > 0, "min_samples must be > 0.")
    _expect(max_samples >= min_samples, "max_samples must be >= min_samples.")
    _expect(float(headroom_percent) >= 0.0, "headroom_percent must be >= 0.")
    _expect(
        float(recommendation_delta_threshold_percent) >= 0.0,
        "recommendation_delta_threshold_percent must be >= 0.",
    )

    valid_samples: list[dict[str, Any]] = []
    invalid_reports: list[dict[str, str]] = []
    used_files: list[str] = []

    for path in report_files:
        try:
            payload = _read_json_object(path)
        except Exception as exc:  # noqa: BLE001
            invalid_reports.append({"path": str(path), "error": str(exc)})
            continue

        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        mttr_value = metrics.get("alert_chain_mttr_seconds")
        try:
            mttr_seconds = float(mttr_value)
        except (TypeError, ValueError):
            invalid_reports.append(
                {
                    "path": str(path),
                    "error": "metrics.alert_chain_mttr_seconds missing or non-numeric",
                }
            )
            continue
        if mttr_seconds < 0.0:
            invalid_reports.append(
                {
                    "path": str(path),
                    "error": "metrics.alert_chain_mttr_seconds must be >= 0",
                }
            )
            continue

        generated_at = _parse_utc_timestamp(str(payload.get("generated_at_utc") or ""))
        valid_samples.append(
            {
                "path": str(path),
                "generated_at_utc": (
                    generated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if generated_at is not None else ""
                ),
                "sort_generated_at": generated_at,
                "mttr_seconds": float(mttr_seconds),
                "reported_target_seconds": (
                    float(metrics.get("mttr_target_seconds"))
                    if metrics.get("mttr_target_seconds") is not None
                    else None
                ),
            }
        )
        used_files.append(str(path))

    valid_samples.sort(
        key=lambda item: item.get("sort_generated_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    selected_samples = valid_samples[: int(max_samples)]
    sample_values = [float(item["mttr_seconds"]) for item in selected_samples]

    recommended_target_seconds: float | None = None
    sample_requirements_met = len(sample_values) >= int(min_samples)
    if sample_values:
        percentile_value = _percentile(sample_values, float(percentile_target))
        recommended_target_seconds = float(percentile_value) * (1.0 + (float(headroom_percent) / 100.0))

    active_target_seconds, active_target_source, active_target_load_error = _resolve_active_target_seconds(
        active_policy_file=active_policy_file
    )
    recommendation_delta_percent: float | None = None
    if recommended_target_seconds is not None and float(active_target_seconds) > 0.0:
        recommendation_delta_percent = abs(float(recommended_target_seconds) - float(active_target_seconds)) / float(
            active_target_seconds
        ) * 100.0
    action_required = bool(
        sample_requirements_met
        and recommendation_delta_percent is not None
        and float(recommendation_delta_percent) > float(recommendation_delta_threshold_percent)
    )
    no_action_required = bool(not action_required)

    if not sample_requirements_met:
        recommended_action = "insufficient_samples_no_action_required"
    elif action_required:
        recommended_action = "watchdog_mttr_target_update_recommended"
    else:
        recommended_action = "no_action_required"

    success = bool(sample_requirements_met or allow_insufficient_samples)

    policy_payload = {
        "version": "1.0.0",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "samples_total": int(len(selected_samples)),
        "calibration_window": {
            "min_samples": int(min_samples),
            "max_samples": int(max_samples),
            "percentile_target": float(percentile_target),
            "headroom_percent": float(headroom_percent),
        },
        "target_mttr_seconds": (
            round(float(recommended_target_seconds), 3)
            if recommended_target_seconds is not None
            else None
        ),
    }

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "output_file": str(output_file),
            "policy_output_file": str(policy_output_file),
        },
        "config": {
            "min_samples": int(min_samples),
            "max_samples": int(max_samples),
            "percentile_target": float(percentile_target),
            "headroom_percent": float(headroom_percent),
            "active_policy_file": str(active_policy_file) if active_policy_file is not None else None,
            "recommendation_delta_threshold_percent": float(recommendation_delta_threshold_percent),
            "write_updates": bool(write_updates),
            "allow_insufficient_samples": bool(allow_insufficient_samples),
        },
        "metrics": {
            "report_files_discovered": int(len(report_files)),
            "report_files_used": int(len(used_files)),
            "report_files_invalid": int(len(invalid_reports)),
            "sample_values_total": int(len(sample_values)),
            "sample_requirements_met": int(1 if sample_requirements_met else 0),
            "recommended_target_seconds": (
                round(float(recommended_target_seconds), 3)
                if recommended_target_seconds is not None
                else None
            ),
            "active_target_seconds": round(float(active_target_seconds), 3),
            "recommendation_delta_percent": (
                round(float(recommendation_delta_percent), 3)
                if recommendation_delta_percent is not None
                else None
            ),
            "action_required": bool(action_required),
            "no_action_required": bool(no_action_required),
            "selected_samples_oldest_generated_at_utc": (
                selected_samples[-1].get("generated_at_utc") if selected_samples else None
            ),
            "selected_samples_newest_generated_at_utc": (
                selected_samples[0].get("generated_at_utc") if selected_samples else None
            ),
        },
        "selected_samples": [
            {
                "path": str(item.get("path") or ""),
                "generated_at_utc": str(item.get("generated_at_utc") or ""),
                "mttr_seconds": float(item.get("mttr_seconds") or 0.0),
                "reported_target_seconds": (
                    float(item.get("reported_target_seconds"))
                    if item.get("reported_target_seconds") is not None
                    else None
                ),
            }
            for item in selected_samples
        ],
        "active_policy": {
            "path": str(active_policy_file) if active_policy_file is not None else None,
            "target_mttr_seconds": round(float(active_target_seconds), 3),
            "source": active_target_source,
            "load_error": active_target_load_error,
        },
        "invalid_reports": invalid_reports,
        "recommended_policy": policy_payload,
        "decision": {
            "sample_requirements_met": bool(sample_requirements_met),
            "action_required": bool(action_required),
            "no_action_required": bool(no_action_required),
            "recommended_action": recommended_action,
            "recommendation_delta_threshold_percent": float(recommendation_delta_threshold_percent),
            "policy_update_applied": bool(write_updates and recommended_target_seconds is not None),
            "policy_update_allowed": bool(write_updates),
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if write_updates:
        policy_output_file.parent.mkdir(parents=True, exist_ok=True)
        policy_output_file.write_text(
            json.dumps(policy_payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    if not success:
        raise RuntimeError(
            "Watchdog rehearsal MTTR calibration failed due to insufficient samples: "
            + json.dumps(report, sort_keys=True)
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate watchdog rehearsal MTTR target from historical drill reports."
    )
    parser.add_argument("--label", default="watchdog-rehearsal-mttr-calibration")
    parser.add_argument("--project-root")
    parser.add_argument("--report-files", default="")
    parser.add_argument("--reports-glob", default="artifacts/master-guard-workflow-health-rehearsal-drill*.json")
    parser.add_argument("--min-samples", type=int, default=10)
    parser.add_argument("--max-samples", type=int, default=14)
    parser.add_argument("--percentile-target", type=float, default=95.0)
    parser.add_argument("--headroom-percent", type=float, default=10.0)
    parser.add_argument("--active-policy-file", default="docs/watchdog-rehearsal-mttr-policy.json")
    parser.add_argument("--recommendation-delta-threshold-percent", type=float, default=10.0)
    parser.add_argument("--output-file", default="artifacts/watchdog-rehearsal-mttr-calibration.json")
    parser.add_argument("--policy-output-file", default="docs/watchdog-rehearsal-mttr-policy.json")
    parser.add_argument("--write-updates", action="store_true")
    parser.add_argument("--allow-insufficient-samples", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    explicit_files = _parse_csv_list(str(args.report_files))
    report_files = _discover_report_files(project_root, explicit_files, str(args.reports_glob))

    try:
        report = run_watchdog_mttr_calibration(
            label=str(args.label),
            project_root=project_root,
            report_files=report_files,
            min_samples=int(args.min_samples),
            max_samples=int(args.max_samples),
            percentile_target=float(args.percentile_target),
            headroom_percent=float(args.headroom_percent),
            active_policy_file=(
                _resolve_path(project_root, str(args.active_policy_file))
                if str(args.active_policy_file or "").strip()
                else None
            ),
            recommendation_delta_threshold_percent=float(args.recommendation_delta_threshold_percent),
            output_file=_resolve_path(project_root, str(args.output_file)),
            policy_output_file=_resolve_path(project_root, str(args.policy_output_file)),
            write_updates=bool(args.write_updates),
            allow_insufficient_samples=bool(args.allow_insufficient_samples),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[watchdog-rehearsal-mttr-calibration] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
