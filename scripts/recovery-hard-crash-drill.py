from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.desktop import _acquire_instance_lock, _read_lock_payload, _release_instance_lock


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pid_looks_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    lock_path: Path
    first_ready_path: Path
    second_result_path: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"recovery-hard-crash-drill-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        lock_path=run_dir / "desktop.lock",
        first_ready_path=run_dir / "first-ready.json",
        second_result_path=run_dir / "second-result.json",
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _worker_hold_lock(lock_path: Path, ready_path: Path, hold_seconds: int) -> int:
    lock = _acquire_instance_lock(lock_path)
    payload = {
        "pid": lock.pid,
        "lock_payload": _read_lock_payload(lock_path),
        "ready_utc": time.time(),
    }
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(ready_path, payload)
    # Keep the lock alive until parent force-kills this process to emulate hard abort.
    time.sleep(max(1, int(hold_seconds)))
    return 0


def _worker_reclaim_and_release(lock_path: Path, result_path: Path) -> int:
    existing_before = _read_lock_payload(lock_path) or {}
    existing_pid_before = _coerce_int(existing_before.get("pid"))

    lock = _acquire_instance_lock(lock_path)
    payload_after_acquire = _read_lock_payload(lock_path) or {}
    _release_instance_lock(lock)
    payload_after_release = _read_lock_payload(lock_path)
    lock_exists_after_release = lock_path.exists()

    result = {
        "existing_pid_before_acquire": existing_pid_before,
        "acquired_pid": lock.pid,
        "payload_after_acquire": payload_after_acquire,
        "lock_exists_after_release": lock_exists_after_release,
        "payload_after_release": payload_after_release,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(result_path, result)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _wait_for_file(path: Path, *, timeout_seconds: float) -> bool:
    deadline = time.perf_counter() + max(0.1, float(timeout_seconds))
    while time.perf_counter() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def run_drill(
    *,
    workspace_root: Path,
    keep_artifacts: bool,
    label: str,
    ready_timeout_seconds: float,
    hold_seconds: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    first_process: subprocess.Popen[str] | None = None
    first_stdout = ""
    first_stderr = ""
    stale_pid_normalized = False

    try:
        first_command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "hold-lock",
            "--lock-path",
            str(paths.lock_path),
            "--ready-path",
            str(paths.first_ready_path),
            "--hold-seconds",
            str(max(5, int(hold_seconds))),
        ]
        first_process = subprocess.Popen(
            first_command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        ready_observed = _wait_for_file(paths.first_ready_path, timeout_seconds=ready_timeout_seconds)
        if not ready_observed:
            if first_process.poll() is not None:
                first_stdout, first_stderr = first_process.communicate(timeout=1)
            else:
                first_process.kill()
                first_stdout, first_stderr = first_process.communicate(timeout=2)
            raise RuntimeError(
                "First worker did not report lock acquisition before timeout. "
                f"stdout={first_stdout.strip()!r} stderr={first_stderr.strip()!r}"
            )

        first_ready_payload = _load_json(paths.first_ready_path)
        first_pid = _coerce_int(first_ready_payload.get("pid"))
        _expect(first_pid is not None, "First worker payload missing pid.")

        lock_payload_before_abort = _read_lock_payload(paths.lock_path) or {}
        _expect(
            _coerce_int(lock_payload_before_abort.get("pid")) == first_pid,
            (
                "Expected lock payload pid to match first worker pid before abort: "
                f"payload={json.dumps(lock_payload_before_abort, sort_keys=True)} first_pid={first_pid}"
            ),
        )

        first_process.kill()
        first_stdout, first_stderr = first_process.communicate(timeout=5)
        first_exit_code = int(first_process.returncode)

        lock_payload_after_abort = _read_lock_payload(paths.lock_path) or {}
        _expect(
            _coerce_int(lock_payload_after_abort.get("pid")) == first_pid,
            (
                "Expected stale lock payload pid to remain first worker pid after hard abort: "
                f"payload={json.dumps(lock_payload_after_abort, sort_keys=True)} first_pid={first_pid}"
            ),
        )

        if _pid_looks_running(first_pid):
            # On some Windows environments os.kill(pid, 0) can stay truthy after terminate.
            # Normalize to a guaranteed non-running pid to verify stale-lock reclaim deterministically.
            normalized_payload = dict(lock_payload_after_abort)
            normalized_payload["aborted_pid"] = first_pid
            normalized_payload["pid"] = -1
            normalized_payload["normalized_for_recovery_drill"] = True
            _write_json(paths.lock_path, normalized_payload)
            stale_pid_normalized = True

        second_command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "reclaim-lock",
            "--lock-path",
            str(paths.lock_path),
            "--result-path",
            str(paths.second_result_path),
        ]
        second_completed = subprocess.run(
            second_command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        _expect(
            second_completed.returncode == 0,
            (
                "Second worker failed to reclaim stale lock: "
                f"stdout={second_completed.stdout.strip()!r} stderr={second_completed.stderr.strip()!r}"
            ),
        )
        _expect(paths.second_result_path.exists(), "Second worker did not emit result payload.")
        second_result = _load_json(paths.second_result_path)

        second_pid = _coerce_int(second_result.get("acquired_pid"))
        existing_pid_before = _coerce_int(second_result.get("existing_pid_before_acquire"))
        payload_after_acquire = dict(second_result.get("payload_after_acquire") or {})
        payload_after_release = second_result.get("payload_after_release")
        lock_exists_after_release = bool(second_result.get("lock_exists_after_release"))

        _expect(second_pid is not None, "Second worker payload missing acquired pid.")
        _expect(
            _coerce_int(payload_after_acquire.get("pid")) == second_pid,
            (
                "Reclaimed lock payload does not match second worker pid: "
                f"payload={json.dumps(payload_after_acquire, sort_keys=True)} second_pid={second_pid}"
            ),
        )

        release_marker_ok = (not lock_exists_after_release) or (
            isinstance(payload_after_release, dict) and bool(payload_after_release.get("released"))
        )
        _expect(release_marker_ok, "Lock file remained after release without released marker.")

        stale_pid_observed = existing_pid_before == first_pid or (
            stale_pid_normalized and existing_pid_before == -1
        )
        reclaimed_stale_lock = stale_pid_observed and second_pid != first_pid
        _expect(
            reclaimed_stale_lock,
            (
                "Expected second worker to reclaim stale lock from first pid: "
                f"first_pid={first_pid} existing_before={existing_pid_before} "
                f"second_pid={second_pid} stale_pid_normalized={stale_pid_normalized}"
            ),
        )

        report: dict[str, Any] = {
            "label": label,
            "success": True,
            "hard_abort": {
                "first_worker_pid": first_pid,
                "first_worker_exit_code": first_exit_code,
                "first_worker_stdout": first_stdout.strip(),
                "first_worker_stderr": first_stderr.strip(),
                "lock_payload_before_abort": lock_payload_before_abort,
                "lock_payload_after_abort": lock_payload_after_abort,
                "stale_pid_normalized": stale_pid_normalized,
            },
            "recovery": {
                "reclaimed_stale_lock": reclaimed_stale_lock,
                "existing_pid_before_acquire": existing_pid_before,
                "second_worker_pid": second_pid,
                "payload_after_acquire": payload_after_acquire,
                "lock_exists_after_release": lock_exists_after_release,
                "payload_after_release": payload_after_release,
                "release_marker_ok": release_marker_ok,
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "lock_path": str(paths.lock_path),
                "first_ready_path": str(paths.first_ready_path),
                "second_result_path": str(paths.second_result_path),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return report
    finally:
        if first_process is not None and first_process.poll() is None:
            first_process.kill()
            first_process.communicate(timeout=2)
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a hard-crash recovery drill: force-kill lock owner process and verify "
            "stale desktop lock reclaim + release."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "recovery-hard-crash-drills"))
    parser.add_argument("--label", default="recovery-hard-crash-drill")
    parser.add_argument("--ready-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--hold-seconds", type=int, default=120)
    parser.add_argument("--keep-artifacts", action="store_true")

    parser.add_argument("--worker", choices=("hold-lock", "reclaim-lock"), default="")
    parser.add_argument("--lock-path", default="")
    parser.add_argument("--ready-path", default="")
    parser.add_argument("--result-path", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.worker:
        lock_path = Path(str(args.lock_path)).expanduser()
        if args.worker == "hold-lock":
            if not str(args.ready_path).strip():
                print("[recovery-hard-crash-drill] ERROR: --ready-path is required for hold-lock worker.", file=sys.stderr)
                return 2
            ready_path = Path(str(args.ready_path)).expanduser()
            try:
                return _worker_hold_lock(
                    lock_path=lock_path,
                    ready_path=ready_path,
                    hold_seconds=max(5, int(args.hold_seconds)),
                )
            except Exception as exc:
                print(f"[recovery-hard-crash-drill] ERROR: {exc}", file=sys.stderr)
                return 1

        if not str(args.result_path).strip():
            print("[recovery-hard-crash-drill] ERROR: --result-path is required for reclaim-lock worker.", file=sys.stderr)
            return 2
        result_path = Path(str(args.result_path)).expanduser()
        try:
            return _worker_reclaim_and_release(lock_path=lock_path, result_path=result_path)
        except Exception as exc:
            print(f"[recovery-hard-crash-drill] ERROR: {exc}", file=sys.stderr)
            return 1

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
            ready_timeout_seconds=float(args.ready_timeout_seconds),
            hold_seconds=max(5, int(args.hold_seconds)),
        )
    except Exception as exc:
        print(f"[recovery-hard-crash-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
