from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _powershell_executable() -> str | None:
    for candidate in ("pwsh", "powershell"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_installer(
    *,
    source_exe_path: Path,
    install_dir: Path,
    expected_sha256: str,
    report_path: Path,
    fail_after_copy: bool = False,
) -> subprocess.CompletedProcess[str]:
    executable = _powershell_executable()
    if executable is None:
        raise RuntimeError(
            "PowerShell executable not found; cannot run install-desktop-update.ps1 for desktop update safety drill."
        )

    command = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PROJECT_ROOT / "scripts" / "install-desktop-update.ps1"),
        "-SourceExePath",
        str(source_exe_path),
        "-InstallDir",
        str(install_dir),
        "-AppName",
        "GoalOpsConsole",
        "-ExpectedSha256",
        expected_sha256,
        "-SkipShortcuts",
        "-ReportPath",
        str(report_path),
    ]
    if fail_after_copy:
        command.append("-FailAfterCopy")

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    install_dir: Path
    source_good: Path
    source_tampered: Path
    source_copy_fail: Path
    report_success: Path
    report_hash_mismatch: Path
    report_copy_fail: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"desktop-update-safety-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        install_dir=run_dir / "install",
        source_good=run_dir / "GoalOpsConsole-good.exe",
        source_tampered=run_dir / "GoalOpsConsole-tampered.exe",
        source_copy_fail=run_dir / "GoalOpsConsole-copy-fail.exe",
        report_success=run_dir / "install-report-success.json",
        report_hash_mismatch=run_dir / "install-report-hash-mismatch.json",
        report_copy_fail=run_dir / "install-report-copy-fail.json",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object at {path}")
    return payload


def run_drill(*, workspace_root: Path, label: str, keep_artifacts: bool) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.install_dir.mkdir(parents=True, exist_ok=True)

    target_exe = paths.install_dir / "GoalOpsConsole.exe"
    backup_exe = paths.install_dir / "GoalOpsConsole-stable.exe"
    helper_script = PROJECT_ROOT / "scripts" / "install-desktop-update.ps1"
    _expect(helper_script.exists(), f"Installer helper script missing: {helper_script}")

    try:
        initial_stable = b"goal-ops-stable-v1"
        target_exe.write_bytes(initial_stable)

        paths.source_good.write_bytes(b"goal-ops-update-v2")
        success_result = _run_installer(
            source_exe_path=paths.source_good,
            install_dir=paths.install_dir,
            expected_sha256=_sha256(paths.source_good),
            report_path=paths.report_success,
        )
        success_report = _read_json(paths.report_success)
        case_success_ok = (
            success_result.returncode == 0
            and target_exe.read_bytes() == paths.source_good.read_bytes()
            and backup_exe.exists()
            and backup_exe.read_bytes() == initial_stable
            and bool(success_report.get("success"))
            and success_report.get("decision") == "update_installed"
        )
        _expect(
            case_success_ok,
            (
                "Successful update case failed. "
                f"rc={success_result.returncode} stdout={success_result.stdout!r} stderr={success_result.stderr!r} "
                f"report={json.dumps(success_report, sort_keys=True)}"
            ),
        )

        stable_before_hash_failure = b"goal-ops-stable-before-hash-failure"
        target_exe.write_bytes(stable_before_hash_failure)
        paths.source_tampered.write_bytes(b"goal-ops-tampered-v3")
        hash_failure_result = _run_installer(
            source_exe_path=paths.source_tampered,
            install_dir=paths.install_dir,
            expected_sha256=hashlib.sha256(b"expected-but-different").hexdigest(),
            report_path=paths.report_hash_mismatch,
        )
        hash_failure_report = _read_json(paths.report_hash_mismatch)
        case_hash_failure_ok = (
            hash_failure_result.returncode != 0
            and target_exe.read_bytes() == stable_before_hash_failure
            and hash_failure_report.get("decision") == "update_failed"
        )
        _expect(
            case_hash_failure_ok,
            (
                "Hash mismatch protection case failed. "
                f"rc={hash_failure_result.returncode} stdout={hash_failure_result.stdout!r} "
                f"stderr={hash_failure_result.stderr!r} report={json.dumps(hash_failure_report, sort_keys=True)}"
            ),
        )

        stable_before_copy_failure = b"goal-ops-stable-before-copy-failure"
        target_exe.write_bytes(stable_before_copy_failure)
        paths.source_copy_fail.write_bytes(b"goal-ops-update-v4")
        copy_failure_result = _run_installer(
            source_exe_path=paths.source_copy_fail,
            install_dir=paths.install_dir,
            expected_sha256=_sha256(paths.source_copy_fail),
            report_path=paths.report_copy_fail,
            fail_after_copy=True,
        )
        copy_failure_report = _read_json(paths.report_copy_fail)
        case_copy_failure_ok = (
            copy_failure_result.returncode != 0
            and target_exe.read_bytes() == stable_before_copy_failure
            and bool(copy_failure_report.get("fallback", {}).get("restored"))
            and copy_failure_report.get("decision") == "rollback_to_stable_restored"
        )
        _expect(
            case_copy_failure_ok,
            (
                "Fallback after copy failure case failed. "
                f"rc={copy_failure_result.returncode} stdout={copy_failure_result.stdout!r} "
                f"stderr={copy_failure_result.stderr!r} report={json.dumps(copy_failure_report, sort_keys=True)}"
            ),
        )

        return {
            "label": label,
            "success": True,
            "cases": {
                "successful_update": {
                    "ok": True,
                    "return_code": success_result.returncode,
                },
                "tampered_hash_blocked": {
                    "ok": True,
                    "return_code": hash_failure_result.returncode,
                },
                "fallback_after_copy_failure": {
                    "ok": True,
                    "return_code": copy_failure_result.returncode,
                },
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "helper_script": str(helper_script),
                "report_success": str(paths.report_success),
                "report_hash_mismatch": str(paths.report_hash_mismatch),
                "report_copy_fail": str(paths.report_copy_fail),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Desktop update safety drill: validate SHA checks, tamper blocking, "
            "and fallback restore to previous stable executable."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "desktop-update-safety-drills"))
    parser.add_argument("--label", default="desktop-update-safety-drill")
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[desktop-update-safety-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
