from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


STRICT_FLAG = "StrictSafeModeUxDegradationCheck"
SKIP_FLAG = "SkipSafeModeUxDegradationCheck"

DEFAULT_TEMPLATE_TOKENS = [
    'id="runtime-state-rail"',
    'id="runtime-state-summary"',
    'id="runtime-state-alerts"',
    'id="runtime-state-recommendations"',
    'data-mutation-control="true"',
]

DEFAULT_APP_JS_TOKENS = [
    "function deriveRuntimeState(",
    "function renderRuntimeStateRail(",
    "function applyMutationControlState(",
    "function ensureMutationAllowed(",
    'api("/system/readiness")',
    'api("/system/slo")',
]

DEFAULT_RUNBOOK_TOKENS = [
    ".\\scripts\\run-safe-mode-ux-degradation-check.ps1",
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


def _read_text(path: Path) -> str:
    _expect(path.exists(), f"Required file not found: {path}")
    return path.read_text(encoding="utf-8")


def _missing_tokens(text: str, tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in text]


def run_check(
    *,
    label: str,
    project_root: Path,
    template_file: Path,
    app_js_file: Path,
    runbook_file: Path,
    release_gate_file: Path,
    ci_workflow_file: Path,
    template_tokens: list[str],
    app_js_tokens: list[str],
    runbook_tokens: list[str],
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    template_text = _read_text(template_file)
    app_js_text = _read_text(app_js_file)
    runbook_text = _read_text(runbook_file)
    release_gate_text = _read_text(release_gate_file)
    ci_workflow_text = _read_text(ci_workflow_file)

    missing_template_tokens = _missing_tokens(template_text, template_tokens)
    missing_app_js_tokens = _missing_tokens(app_js_text, app_js_tokens)
    missing_runbook_tokens = _missing_tokens(runbook_text, runbook_tokens)

    release_gate_has_strict_flag = f"${STRICT_FLAG}" in release_gate_text
    release_gate_has_skip_flag = f"${SKIP_FLAG}" in release_gate_text
    ci_has_strict_flag = f"-{STRICT_FLAG}" in ci_workflow_text
    runbook_has_strict_flag = f"-{STRICT_FLAG}" in runbook_text

    success = (
        not missing_template_tokens
        and not missing_app_js_tokens
        and not missing_runbook_tokens
        and release_gate_has_strict_flag
        and release_gate_has_skip_flag
        and ci_has_strict_flag
        and runbook_has_strict_flag
    )

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "template_file": str(template_file),
            "app_js_file": str(app_js_file),
            "runbook_file": str(runbook_file),
            "release_gate_file": str(release_gate_file),
            "ci_workflow_file": str(ci_workflow_file),
            "output_file": str(output_file),
        },
        "checks": {
            "template_tokens": template_tokens,
            "app_js_tokens": app_js_tokens,
            "runbook_tokens": runbook_tokens,
            "missing_template_tokens": missing_template_tokens,
            "missing_app_js_tokens": missing_app_js_tokens,
            "missing_runbook_tokens": missing_runbook_tokens,
            "release_gate_has_strict_flag": release_gate_has_strict_flag,
            "release_gate_has_skip_flag": release_gate_has_skip_flag,
            "ci_has_strict_flag": ci_has_strict_flag,
            "runbook_has_strict_flag": runbook_has_strict_flag,
        },
        "metrics": {
            "template_token_count": len(template_tokens),
            "template_missing_count": len(missing_template_tokens),
            "app_js_token_count": len(app_js_tokens),
            "app_js_missing_count": len(missing_app_js_tokens),
            "runbook_token_count": len(runbook_tokens),
            "runbook_missing_count": len(missing_runbook_tokens),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
            "strict_flag": STRICT_FLAG,
            "skip_flag": SKIP_FLAG,
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
        raise RuntimeError(f"Safe-mode UX degradation contract failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify safe-mode/degraded-state UX contract wiring across dashboard template, frontend "
            "runtime logic, release gate flags, CI strict flag, and runbook command references."
        )
    )
    parser.add_argument("--label", default="safe-mode-ux-degradation-check")
    parser.add_argument("--project-root")
    parser.add_argument("--template-file", default="goal_ops_console/templates/index.html")
    parser.add_argument("--app-js-file", default="goal_ops_console/static/app.js")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--release-gate-file", default="scripts/release-gate.ps1")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--template-tokens", default=",".join(DEFAULT_TEMPLATE_TOKENS))
    parser.add_argument("--app-js-tokens", default=",".join(DEFAULT_APP_JS_TOKENS))
    parser.add_argument("--runbook-tokens", default=",".join(DEFAULT_RUNBOOK_TOKENS))
    parser.add_argument("--output-file", default="artifacts/safe-mode-ux-degradation-report.json")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = (
        _resolve_path(inferred_project_root, args.project_root)
        if args.project_root
        else inferred_project_root
    )
    template_file = _resolve_path(project_root, args.template_file)
    app_js_file = _resolve_path(project_root, args.app_js_file)
    runbook_file = _resolve_path(project_root, args.runbook_file)
    release_gate_file = _resolve_path(project_root, args.release_gate_file)
    ci_workflow_file = _resolve_path(project_root, args.ci_workflow_file)
    output_file = _resolve_path(project_root, args.output_file)

    template_tokens = _parse_csv_list(args.template_tokens)
    app_js_tokens = _parse_csv_list(args.app_js_tokens)
    runbook_tokens = _parse_csv_list(args.runbook_tokens)
    if not template_tokens:
        print("[safe-mode-ux-degradation-check] ERROR: --template-tokens must not be empty.", file=sys.stderr)
        return 2
    if not app_js_tokens:
        print("[safe-mode-ux-degradation-check] ERROR: --app-js-tokens must not be empty.", file=sys.stderr)
        return 2
    if not runbook_tokens:
        print("[safe-mode-ux-degradation-check] ERROR: --runbook-tokens must not be empty.", file=sys.stderr)
        return 2

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            template_file=template_file,
            app_js_file=app_js_file,
            runbook_file=runbook_file,
            release_gate_file=release_gate_file,
            ci_workflow_file=ci_workflow_file,
            template_tokens=template_tokens,
            app_js_tokens=app_js_tokens,
            runbook_tokens=runbook_tokens,
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[safe-mode-ux-degradation-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
