from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


STRICT_FLAG = "StrictA11yTestHarnessCheck"
SKIP_FLAG = "SkipA11yTestHarnessCheck"

DEFAULT_TEMPLATE_TOKENS = [
    'class="skip-link"',
    'href="#main-content"',
    'id="main-content"',
    'tabindex="-1"',
    'id="runtime-state-rail"',
    'id="runtime-state-summary"',
    'id="runtime-state-alerts"',
    'id="runtime-state-recommendations"',
    'role="status"',
    'aria-live="polite"',
]

DEFAULT_APP_JS_TOKENS = [
    "function isTextInputTarget(",
    "function handleDesktopShortcut(",
    'document.addEventListener("keydown"',
    "event.preventDefault();",
    "filterInput?.focus();",
    'target.setAttribute("aria-live", error ? "assertive" : "polite");',
]

DEFAULT_RUNBOOK_TOKENS = [
    ".\\scripts\\run-a11y-test-harness-check.ps1",
]

DEFAULT_CONTRAST_SPEC = (
    "ink:panel:7.0,"
    "muted:panel:4.5,"
    "info:panel:4.5,"
    "good:panel:4.5,"
    "bad:panel:4.5,"
    "warn:panel:3.0"
)


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


def _extract_style_block(template_text: str) -> str:
    match = re.search(r"<style>(.*?)</style>", template_text, flags=re.IGNORECASE | re.DOTALL)
    _expect(match is not None, "Template does not contain an inline <style> block.")
    return str(match.group(1))


def _extract_css_vars(block_text: str) -> dict[str, str]:
    return {
        str(match.group(1)).strip(): str(match.group(2)).strip()
        for match in re.finditer(r"--([a-zA-Z0-9_-]+)\s*:\s*([^;]+);", block_text)
    }


def _extract_css_block(css_text: str, selector_regex: str) -> str:
    match = re.search(selector_regex, css_text, flags=re.IGNORECASE | re.DOTALL)
    _expect(match is not None, f"CSS block not found for selector regex: {selector_regex}")
    return str(match.group(1))


def _hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    normalized = str(value).strip().lower()
    if not normalized.startswith("#"):
        return None
    hex_part = normalized[1:]
    if len(hex_part) == 3:
        hex_part = "".join(ch * 2 for ch in hex_part)
    if len(hex_part) != 6:
        return None
    try:
        channels = tuple(int(hex_part[idx : idx + 2], 16) / 255.0 for idx in (0, 2, 4))
    except ValueError:
        return None
    return channels


def _linearize(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    red, green, blue = rgb
    return 0.2126 * _linearize(red) + 0.7152 * _linearize(green) + 0.0722 * _linearize(blue)


def _contrast_ratio(foreground: tuple[float, float, float], background: tuple[float, float, float]) -> float:
    lum_fg = _relative_luminance(foreground)
    lum_bg = _relative_luminance(background)
    lighter = max(lum_fg, lum_bg)
    darker = min(lum_fg, lum_bg)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_contrast_spec(spec: str) -> list[tuple[str, str, float]]:
    pairs: list[tuple[str, str, float]] = []
    for item in _parse_csv_list(spec):
        parts = [segment.strip() for segment in item.split(":")]
        _expect(len(parts) == 3, f"Invalid contrast spec item: {item!r}. Expected format fg:bg:min_ratio.")
        fg, bg, threshold_text = parts
        _expect(bool(fg), f"Invalid contrast spec item (missing fg): {item!r}")
        _expect(bool(bg), f"Invalid contrast spec item (missing bg): {item!r}")
        try:
            threshold = float(threshold_text)
        except ValueError as exc:
            raise RuntimeError(f"Invalid contrast threshold in spec item {item!r}: {exc}") from exc
        _expect(threshold > 0, f"Contrast threshold must be positive in spec item: {item!r}")
        pairs.append((fg, bg, threshold))
    _expect(bool(pairs), "At least one contrast pair is required.")
    return pairs


def _contrast_checks(
    css_text: str,
    pairs: list[tuple[str, str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root_vars = _extract_css_vars(_extract_css_block(css_text, r":root\s*\{(.*?)\}"))
    graphite_vars = _extract_css_vars(_extract_css_block(css_text, r"body\.visual-graphite\s*\{(.*?)\}"))
    signal_vars = _extract_css_vars(_extract_css_block(css_text, r"body\.visual-signal\s*\{(.*?)\}"))

    palettes = {
        "warm": {**root_vars},
        "graphite": {**root_vars, **graphite_vars},
        "signal": {**root_vars, **signal_vars},
    }
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for palette_name, palette in palettes.items():
        for fg_var, bg_var, min_ratio in pairs:
            fg_raw = palette.get(fg_var)
            bg_raw = palette.get(bg_var)
            record: dict[str, Any] = {
                "palette": palette_name,
                "foreground": fg_var,
                "background": bg_var,
                "min_ratio": min_ratio,
                "foreground_raw": fg_raw,
                "background_raw": bg_raw,
            }
            if fg_raw is None or bg_raw is None:
                record["ok"] = False
                record["error"] = "missing_css_variable"
                failures.append(record)
                results.append(record)
                continue
            fg_rgb = _hex_to_rgb(fg_raw)
            bg_rgb = _hex_to_rgb(bg_raw)
            if fg_rgb is None or bg_rgb is None:
                record["ok"] = False
                record["error"] = "unsupported_color_format"
                failures.append(record)
                results.append(record)
                continue
            ratio = _contrast_ratio(fg_rgb, bg_rgb)
            record["ratio"] = round(ratio, 4)
            record["ok"] = bool(ratio >= min_ratio)
            if not record["ok"]:
                failures.append(record)
            results.append(record)
    return results, failures


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
    contrast_spec: list[tuple[str, str, float]],
    min_sr_only_labels: int,
    min_aria_live_regions: int,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    template_text = _read_text(template_file)
    app_js_text = _read_text(app_js_file)
    runbook_text = _read_text(runbook_file)
    release_gate_text = _read_text(release_gate_file)
    ci_workflow_text = _read_text(ci_workflow_file)
    css_text = _extract_style_block(template_text)

    missing_template_tokens = _missing_tokens(template_text, template_tokens)
    missing_app_js_tokens = _missing_tokens(app_js_text, app_js_tokens)
    missing_runbook_tokens = _missing_tokens(runbook_text, runbook_tokens)

    sr_only_label_count = len(
        re.findall(r'<label[^>]*class="[^"]*sr-only[^"]*"[^>]*\sfor="[^"]+"', template_text, flags=re.IGNORECASE)
    )
    aria_live_count = len(re.findall(r'aria-live="(?:polite|assertive)"', template_text, flags=re.IGNORECASE))

    contrast_results, contrast_failures = _contrast_checks(css_text, contrast_spec)

    release_gate_has_strict_flag = f"${STRICT_FLAG}" in release_gate_text
    release_gate_has_skip_flag = f"${SKIP_FLAG}" in release_gate_text
    ci_has_strict_flag = f"-{STRICT_FLAG}" in ci_workflow_text
    runbook_has_strict_flag = f"-{STRICT_FLAG}" in runbook_text

    success = (
        not missing_template_tokens
        and not missing_app_js_tokens
        and not missing_runbook_tokens
        and sr_only_label_count >= int(min_sr_only_labels)
        and aria_live_count >= int(min_aria_live_regions)
        and not contrast_failures
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
            "sr_only_label_count": sr_only_label_count,
            "min_sr_only_labels": int(min_sr_only_labels),
            "aria_live_count": aria_live_count,
            "min_aria_live_regions": int(min_aria_live_regions),
            "contrast_results": contrast_results,
            "contrast_failures": contrast_failures,
            "release_gate_has_strict_flag": release_gate_has_strict_flag,
            "release_gate_has_skip_flag": release_gate_has_skip_flag,
            "ci_has_strict_flag": ci_has_strict_flag,
            "runbook_has_strict_flag": runbook_has_strict_flag,
        },
        "metrics": {
            "template_missing_count": len(missing_template_tokens),
            "app_js_missing_count": len(missing_app_js_tokens),
            "runbook_missing_count": len(missing_runbook_tokens),
            "contrast_failures_count": len(contrast_failures),
            "contrast_checks_count": len(contrast_results),
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
        raise RuntimeError(f"A11y test harness check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate keyboard/screen-reader/contrast baseline contracts for dashboard UI "
            "and verify release-gate/CI/runbook strict-flag wiring."
        )
    )
    parser.add_argument("--label", default="a11y-test-harness-check")
    parser.add_argument("--project-root")
    parser.add_argument("--template-file", default="goal_ops_console/templates/index.html")
    parser.add_argument("--app-js-file", default="goal_ops_console/static/app.js")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--release-gate-file", default="scripts/release-gate.ps1")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--template-tokens", default=",".join(DEFAULT_TEMPLATE_TOKENS))
    parser.add_argument("--app-js-tokens", default=",".join(DEFAULT_APP_JS_TOKENS))
    parser.add_argument("--runbook-tokens", default=",".join(DEFAULT_RUNBOOK_TOKENS))
    parser.add_argument("--contrast-spec", default=DEFAULT_CONTRAST_SPEC)
    parser.add_argument("--min-sr-only-labels", type=int, default=10)
    parser.add_argument("--min-aria-live-regions", type=int, default=5)
    parser.add_argument("--output-file", default="artifacts/a11y-test-harness-release-gate.json")
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
    contrast_spec = _parse_contrast_spec(args.contrast_spec)

    if not template_tokens:
        print("[a11y-test-harness-check] ERROR: --template-tokens must not be empty.", file=sys.stderr)
        return 2
    if not app_js_tokens:
        print("[a11y-test-harness-check] ERROR: --app-js-tokens must not be empty.", file=sys.stderr)
        return 2
    if not runbook_tokens:
        print("[a11y-test-harness-check] ERROR: --runbook-tokens must not be empty.", file=sys.stderr)
        return 2
    if int(args.min_sr_only_labels) < 1:
        print("[a11y-test-harness-check] ERROR: --min-sr-only-labels must be >= 1.", file=sys.stderr)
        return 2
    if int(args.min_aria_live_regions) < 1:
        print("[a11y-test-harness-check] ERROR: --min-aria-live-regions must be >= 1.", file=sys.stderr)
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
            contrast_spec=contrast_spec,
            min_sr_only_labels=int(args.min_sr_only_labels),
            min_aria_live_regions=int(args.min_aria_live_regions),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[a11y-test-harness-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
