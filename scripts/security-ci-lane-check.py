from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_vulnerability_id(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_vulnerability_ids(vulnerability: dict[str, Any]) -> set[str]:
    ids = {_normalize_vulnerability_id(vulnerability.get("id"))}
    aliases = vulnerability.get("aliases")
    if isinstance(aliases, list):
        ids.update(_normalize_vulnerability_id(alias) for alias in aliases)
    return {item for item in ids if item}


def _summarize_dependency_vulnerabilities(payload: Any, ignored_vulnerability_ids: set[str]) -> dict[str, int]:
    dependencies: list[dict[str, Any]] = []
    if isinstance(payload, list):
        dependencies = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        maybe_dependencies = payload.get("dependencies")
        if isinstance(maybe_dependencies, list):
            dependencies = [item for item in maybe_dependencies if isinstance(item, dict)]

    vulnerability_count = 0
    ignored_vulnerability_count = 0
    for dependency in dependencies:
        vulns = dependency.get("vulns")
        if not isinstance(vulns, list):
            vulns = dependency.get("vulnerabilities")
        if isinstance(vulns, list):
            for vulnerability in vulns:
                if not isinstance(vulnerability, dict):
                    vulnerability_count += 1
                    continue
                if _extract_vulnerability_ids(vulnerability) & ignored_vulnerability_ids:
                    ignored_vulnerability_count += 1
                else:
                    vulnerability_count += 1
    return {
        "vulnerability_count": vulnerability_count,
        "ignored_vulnerability_count": ignored_vulnerability_count,
    }


def _extract_bandit_counts(payload: Any) -> dict[str, int]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return {"high": 0, "medium": 0, "low": 0, "total": 0}

    counts = {"high": 0, "medium": 0, "low": 0, "total": 0}
    for item in results:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("issue_severity") or "").strip().lower()
        if severity not in {"high", "medium", "low"}:
            continue
        counts[severity] += 1
        counts["total"] += 1
    return counts


def _extract_requirement_name(requirement: str) -> str:
    token = str(requirement or "").strip()
    if not token:
        return ""
    for marker in (";", "[", " ", "<", ">", "=", "!", "~"):
        index = token.find(marker)
        if index > 0:
            token = token[:index]
            break
    return token.strip()


def _load_project_metadata(project_root: Path) -> dict[str, Any]:
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        raise RuntimeError(f"Missing pyproject.toml: {pyproject_path}")
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11+ is required for tomllib support.")

    import tomllib  # pylint: disable=import-outside-toplevel

    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise RuntimeError("pyproject.toml is missing [project] metadata.")
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        dependencies = []
    return {
        "name": str(project.get("name") or "unknown-project"),
        "version": str(project.get("version") or "0.0.0"),
        "dependencies": [str(item) for item in dependencies if str(item).strip()],
    }


def _build_sbom(project_root: Path, output_file: Path, *, label: str) -> dict[str, Any]:
    metadata = _load_project_metadata(project_root)
    installed_versions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = str(distribution.metadata.get("Name") or "").strip()
        version = str(distribution.version or "").strip()
        if name and version:
            installed_versions[name.lower()] = version

    components: list[dict[str, Any]] = []
    for raw_requirement in metadata["dependencies"]:
        package_name = _extract_requirement_name(raw_requirement)
        if not package_name:
            continue
        components.append(
            {
                "name": package_name,
                "version": installed_versions.get(package_name.lower(), "unknown"),
                "requirement": raw_requirement,
                "scope": "runtime",
            }
        )

    sbom = {
        "label": str(label),
        "success": True,
        "format": "goal-ops-sbom-v1",
        "generated_at_utc": _utc_now(),
        "project": {
            "name": metadata["name"],
            "version": metadata["version"],
        },
        "components": components,
        "component_count": len(components),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(sbom, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return sbom


def _run_json_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> tuple[subprocess.CompletedProcess[str], Any | None, str | None]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_seconds)),
    )
    stdout = str(completed.stdout or "").strip()
    if not stdout:
        return completed, None, "No JSON output produced."
    try:
        return completed, json.loads(stdout), None
    except json.JSONDecodeError as exc:
        return completed, None, f"Invalid JSON output: {exc}"


def run_check(
    *,
    label: str,
    project_root: Path,
    python_exe: str,
    deployment_profile: str,
    scan_path: str,
    max_dependency_vulnerabilities: int,
    max_sast_high: int,
    max_sast_medium: int,
    skip_dependency_audit: bool,
    skip_sast: bool,
    skip_sbom: bool,
    allow_missing_tools: bool,
    timeout_seconds: int,
    dependency_audit_json_file: Path | None,
    sast_json_file: Path | None,
    sbom_output_file: Path,
    ignored_dependency_vulnerabilities: list[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = str(deployment_profile).strip().lower() or "production"
    ignored_dependency_vulnerability_ids = {
        _normalize_vulnerability_id(item) for item in (ignored_dependency_vulnerabilities or [])
    }
    ignored_dependency_vulnerability_ids.discard("")
    ignored_dependency_vulnerability_display = sorted(ignored_dependency_vulnerability_ids)

    dependency_report: dict[str, Any] = {
        "enabled": not skip_dependency_audit,
        "tool": "pip-audit",
        "tool_available": True,
        "executed": False,
        "source": "command",
        "return_code": None,
        "vulnerability_count": 0,
        "ignored_vulnerability_count": 0,
        "ignored_vulnerability_ids": ignored_dependency_vulnerability_display,
        "error": None,
    }
    sast_report: dict[str, Any] = {
        "enabled": not skip_sast,
        "tool": "bandit",
        "tool_available": True,
        "executed": False,
        "source": "command",
        "return_code": None,
        "high_count": 0,
        "medium_count": 0,
        "low_count": 0,
        "total_count": 0,
        "error": None,
    }
    sbom_report: dict[str, Any] = {
        "enabled": not skip_sbom,
        "generated": False,
        "output_file": str(sbom_output_file),
        "component_count": 0,
        "error": None,
    }

    if not skip_dependency_audit:
        if dependency_audit_json_file is not None:
            dependency_report["source"] = "file"
            payload = _read_json_file(dependency_audit_json_file)
            summary = _summarize_dependency_vulnerabilities(payload, ignored_dependency_vulnerability_ids)
            dependency_report["vulnerability_count"] = summary["vulnerability_count"]
            dependency_report["ignored_vulnerability_count"] = summary["ignored_vulnerability_count"]
        else:
            command = [python_exe, "-m", "pip_audit", "-f", "json"]
            try:
                completed, payload, error = _run_json_command(
                    command,
                    cwd=project_root,
                    timeout_seconds=timeout_seconds,
                )
                dependency_report["executed"] = True
                dependency_report["return_code"] = int(completed.returncode)
                if error is None and payload is not None:
                    summary = _summarize_dependency_vulnerabilities(payload, ignored_dependency_vulnerability_ids)
                    dependency_report["vulnerability_count"] = summary["vulnerability_count"]
                    dependency_report["ignored_vulnerability_count"] = summary["ignored_vulnerability_count"]
                else:
                    stderr = str(completed.stderr or "").strip()
                    if "No module named pip_audit" in stderr:
                        dependency_report["tool_available"] = False
                    dependency_report["error"] = error or stderr or "Unknown pip-audit error."
                if int(completed.returncode) not in {0, 1}:
                    if dependency_report["error"] is None:
                        dependency_report["error"] = (
                            f"pip-audit failed with unexpected return code {completed.returncode}."
                        )
            except Exception as exc:  # pragma: no cover - defensive
                dependency_report["tool_available"] = False
                dependency_report["error"] = str(exc)

    if not skip_sast:
        if sast_json_file is not None:
            sast_report["source"] = "file"
            payload = _read_json_file(sast_json_file)
            counts = _extract_bandit_counts(payload)
            sast_report["high_count"] = counts["high"]
            sast_report["medium_count"] = counts["medium"]
            sast_report["low_count"] = counts["low"]
            sast_report["total_count"] = counts["total"]
        else:
            command = [python_exe, "-m", "bandit", "-r", scan_path, "-f", "json", "-q"]
            try:
                completed, payload, error = _run_json_command(
                    command,
                    cwd=project_root,
                    timeout_seconds=timeout_seconds,
                )
                sast_report["executed"] = True
                sast_report["return_code"] = int(completed.returncode)
                if error is None and payload is not None:
                    counts = _extract_bandit_counts(payload)
                    sast_report["high_count"] = counts["high"]
                    sast_report["medium_count"] = counts["medium"]
                    sast_report["low_count"] = counts["low"]
                    sast_report["total_count"] = counts["total"]
                else:
                    stderr = str(completed.stderr or "").strip()
                    if "No module named bandit" in stderr:
                        sast_report["tool_available"] = False
                    sast_report["error"] = error or stderr or "Unknown bandit error."
                if int(completed.returncode) not in {0, 1}:
                    if sast_report["error"] is None:
                        sast_report["error"] = (
                            f"bandit failed with unexpected return code {completed.returncode}."
                        )
            except Exception as exc:  # pragma: no cover - defensive
                sast_report["tool_available"] = False
                sast_report["error"] = str(exc)

    if not skip_sbom:
        try:
            sbom_payload = _build_sbom(project_root, sbom_output_file, label=label)
            sbom_report["generated"] = True
            sbom_report["component_count"] = int(sbom_payload.get("component_count") or 0)
        except Exception as exc:  # pragma: no cover - defensive
            sbom_report["error"] = str(exc)

    criteria: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "details": details})

    if profile == "production":
        if not skip_dependency_audit:
            add(
                "dependency_audit_tool_available",
                bool(dependency_report["tool_available"]) or allow_missing_tools,
                (
                    f"tool_available={dependency_report['tool_available']}, "
                    f"allow_missing_tools={allow_missing_tools}"
                ),
            )
            add(
                "dependency_vulnerability_budget",
                int(dependency_report["vulnerability_count"]) <= max(0, int(max_dependency_vulnerabilities)),
                (
                    f"vulnerability_count={dependency_report['vulnerability_count']}, "
                    f"ignored_vulnerability_count={dependency_report['ignored_vulnerability_count']}, "
                    f"max={max_dependency_vulnerabilities}"
                ),
            )
            add(
                "dependency_audit_execution_error",
                dependency_report["error"] is None
                or (allow_missing_tools and dependency_report["tool_available"] is False),
                f"error={dependency_report['error']!r}",
            )

        if not skip_sast:
            add(
                "sast_tool_available",
                bool(sast_report["tool_available"]) or allow_missing_tools,
                (
                    f"tool_available={sast_report['tool_available']}, "
                    f"allow_missing_tools={allow_missing_tools}"
                ),
            )
            add(
                "sast_high_budget",
                int(sast_report["high_count"]) <= max(0, int(max_sast_high)),
                f"high_count={sast_report['high_count']}, max={max_sast_high}",
            )
            add(
                "sast_medium_budget",
                int(sast_report["medium_count"]) <= max(0, int(max_sast_medium)),
                f"medium_count={sast_report['medium_count']}, max={max_sast_medium}",
            )
            add(
                "sast_execution_error",
                sast_report["error"] is None
                or (allow_missing_tools and sast_report["tool_available"] is False),
                f"error={sast_report['error']!r}",
            )

        if not skip_sbom:
            add(
                "sbom_generated",
                bool(sbom_report["generated"]),
                f"generated={sbom_report['generated']}, error={sbom_report['error']!r}",
            )
            add(
                "sbom_component_count",
                int(sbom_report["component_count"]) > 0,
                f"component_count={sbom_report['component_count']}",
            )
    else:
        add(
            "non_production_profile",
            True,
            f"deployment_profile={profile!r} (hard requirements skipped)",
        )

    failed = [item for item in criteria if not item["passed"]]
    success = len(failed) == 0
    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "project_root": str(project_root),
            "deployment_profile": profile,
            "python_exe": str(python_exe),
            "scan_path": str(scan_path),
            "max_dependency_vulnerabilities": int(max_dependency_vulnerabilities),
            "ignored_dependency_vulnerabilities": ignored_dependency_vulnerability_display,
            "max_sast_high": int(max_sast_high),
            "max_sast_medium": int(max_sast_medium),
            "skip_dependency_audit": bool(skip_dependency_audit),
            "skip_sast": bool(skip_sast),
            "skip_sbom": bool(skip_sbom),
            "allow_missing_tools": bool(allow_missing_tools),
            "timeout_seconds": int(timeout_seconds),
            "dependency_audit_json_file": (
                str(dependency_audit_json_file) if dependency_audit_json_file is not None else None
            ),
            "sast_json_file": str(sast_json_file) if sast_json_file is not None else None,
            "sbom_output_file": str(sbom_output_file),
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_failed": len(failed),
            "criteria_passed": len(criteria) - len(failed),
            "dependency_vulnerability_count": int(dependency_report["vulnerability_count"]),
            "dependency_ignored_vulnerability_count": int(dependency_report["ignored_vulnerability_count"]),
            "sast_high_count": int(sast_report["high_count"]),
            "sast_medium_count": int(sast_report["medium_count"]),
            "sast_low_count": int(sast_report["low_count"]),
            "sbom_component_count": int(sbom_report["component_count"]),
        },
        "criteria": criteria,
        "failed_criteria": failed,
        "dependency_audit": dependency_report,
        "sast": sast_report,
        "sbom": sbom_report,
        "generated_at_utc": _utc_now(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Security CI lane check for dependency scanning, SAST, and SBOM export with "
            "deterministic pass/fail policy."
        )
    )
    parser.add_argument("--label", default="security-ci-lane")
    parser.add_argument("--project-root")
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--scan-path", default="goal_ops_console")
    parser.add_argument("--max-dependency-vulnerabilities", type=int, default=0)
    parser.add_argument("--ignore-dependency-vulnerability", action="append", default=[])
    parser.add_argument("--max-sast-high", type=int, default=0)
    parser.add_argument("--max-sast-medium", type=int, default=200)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--skip-dependency-audit", action="store_true")
    parser.add_argument("--skip-sast", action="store_true")
    parser.add_argument("--skip-sbom", action="store_true")
    parser.add_argument("--allow-missing-tools", action="store_true")
    parser.add_argument("--dependency-audit-json-file")
    parser.add_argument("--sast-json-file")
    parser.add_argument("--sbom-output-file", default="artifacts/security-sbom-check.json")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = Path(str(args.project_root)).expanduser() if args.project_root else inferred_project_root
    if not project_root.is_absolute():
        project_root = (inferred_project_root / project_root).resolve()

    dependency_audit_json_file = (
        Path(str(args.dependency_audit_json_file)).expanduser()
        if args.dependency_audit_json_file
        else None
    )
    if dependency_audit_json_file is not None and not dependency_audit_json_file.is_absolute():
        dependency_audit_json_file = (project_root / dependency_audit_json_file).resolve()

    sast_json_file = Path(str(args.sast_json_file)).expanduser() if args.sast_json_file else None
    if sast_json_file is not None and not sast_json_file.is_absolute():
        sast_json_file = (project_root / sast_json_file).resolve()

    sbom_output_file = Path(str(args.sbom_output_file)).expanduser()
    if not sbom_output_file.is_absolute():
        sbom_output_file = (project_root / sbom_output_file).resolve()

    report = run_check(
        label=str(args.label),
        project_root=project_root,
        python_exe=str(args.python_exe),
        deployment_profile=str(args.deployment_profile),
        scan_path=str(args.scan_path),
        max_dependency_vulnerabilities=int(args.max_dependency_vulnerabilities),
        max_sast_high=int(args.max_sast_high),
        max_sast_medium=int(args.max_sast_medium),
        skip_dependency_audit=bool(args.skip_dependency_audit),
        skip_sast=bool(args.skip_sast),
        skip_sbom=bool(args.skip_sbom),
        allow_missing_tools=bool(args.allow_missing_tools),
        timeout_seconds=int(args.timeout_seconds),
        dependency_audit_json_file=dependency_audit_json_file,
        sast_json_file=sast_json_file,
        sbom_output_file=sbom_output_file,
        ignored_dependency_vulnerabilities=[str(item) for item in args.ignore_dependency_vulnerability],
    )

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[security-ci-lane-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
