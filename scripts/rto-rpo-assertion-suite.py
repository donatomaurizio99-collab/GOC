from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.database import Database, new_id, now_utc
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"Required file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _integrity_report(conn: sqlite3.Connection) -> dict[str, Any]:
    quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    full_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
    quick_ok = len(quick_rows) == 1 and quick_rows[0].lower() == "ok"
    full_ok = len(full_rows) == 1 and full_rows[0].lower() == "ok"
    return {
        "quick_ok": quick_ok,
        "quick_result": "ok" if quick_ok else "; ".join(quick_rows) if quick_rows else "no result",
        "full_ok": full_ok,
        "full_result": "ok" if full_ok else "; ".join(full_rows) if full_rows else "no result",
    }


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    table_rows = conn.execute(
        """SELECT name
           FROM sqlite_master
           WHERE type = 'table'
           AND name NOT LIKE 'sqlite_%'
           ORDER BY name ASC"""
    ).fetchall()
    counts: dict[str, int] = {}
    for row in table_rows:
        table_name = str(row[0])
        query = f"SELECT COUNT(*) FROM {_quoted_identifier(table_name)}"
        counts[table_name] = int(conn.execute(query).fetchone()[0])
    return counts


def _total_rows(counts: dict[str, int]) -> int:
    return int(sum(int(value) for value in counts.values()))


def _snapshot(database_path: Path) -> dict[str, Any]:
    conn = _connect(database_path)
    try:
        counts = _table_counts(conn)
        integrity = _integrity_report(conn)
    finally:
        conn.close()
    return {
        "counts": counts,
        "total_rows": _total_rows(counts),
        "integrity": integrity,
    }


def _copy_database(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    source_conn = _connect(source_path)
    target_conn = _connect(target_path)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def _seed_rows(*, db_path: Path, row_count: int, phase: str, start_index: int) -> dict[str, int]:
    db = Database(str(db_path))
    db.initialize()

    inserted = {
        "goals": 0,
        "goal_queue": 0,
        "tasks": 0,
        "task_state": 0,
        "events": 0,
        "workflow_runs": 0,
    }

    workflow_id = "drill.rto_rpo_assertion"
    timestamp = now_utc()
    db.execute(
        """INSERT OR IGNORE INTO workflow_definitions
           (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
        workflow_id,
        "RTO/RPO Assertion Drill",
        "Synthetic workflow rows for RTO/RPO assertion suite",
        "maintenance.retention_cleanup",
        timestamp,
        timestamp,
    )

    for offset in range(max(0, int(row_count))):
        index = int(start_index) + offset
        ts = now_utc()
        goal_id = new_id()
        task_id = new_id()
        event_id = new_id()
        run_id = new_id()
        correlation_id = f"{phase}:{goal_id}:{index}"

        db.execute(
            """INSERT INTO goals
               (goal_id, title, description, state, blocked_reason, escalation_reason, version, created_at, updated_at)
               VALUES (?, ?, ?, 'active', NULL, NULL, 1, ?, ?)""",
            goal_id,
            f"RTO/RPO Goal {phase}-{index}",
            "Seeded by rto-rpo assertion suite",
            ts,
            ts,
        )
        inserted["goals"] += 1

        db.execute(
            """INSERT INTO goal_queue
               (goal_id, urgency, value, deadline_score, base_priority, priority, wait_cycles, force_promoted,
                status, version, created_at, updated_at)
               VALUES (?, 0.6, 0.5, 0.3, 0.47, 0.47, 0, 0, 'active', 1, ?, ?)""",
            goal_id,
            ts,
            ts,
        )
        inserted["goal_queue"] += 1

        db.execute(
            """INSERT INTO tasks
               (task_id, goal_id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            task_id,
            goal_id,
            f"RTO/RPO Task {phase}-{index}",
            ts,
            ts,
        )
        inserted["tasks"] += 1

        db.execute(
            """INSERT INTO task_state
               (task_id, goal_id, correlation_id, status, retry_count, failure_type, error_hash,
                version, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', 0, NULL, NULL, 1, ?, ?)""",
            task_id,
            goal_id,
            correlation_id,
            ts,
            ts,
        )
        inserted["task_state"] += 1

        db.execute(
            """INSERT INTO events
               (event_id, event_type, entity_id, correlation_id, payload, emitted_at)
               VALUES (?, 'drill.rto_rpo.seeded', ?, ?, ?, ?)""",
            event_id,
            goal_id,
            correlation_id,
            json.dumps({"phase": phase, "index": index}, ensure_ascii=True, sort_keys=True),
            ts,
        )
        inserted["events"] += 1

        db.execute(
            """INSERT INTO workflow_runs
               (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                input_payload, result_payload, started_at, finished_at, created_at, updated_at)
               VALUES (?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            run_id,
            workflow_id,
            "rto-rpo-assertion-suite",
            f"workflow:{workflow_id}:{run_id[:8]}",
            f"{phase}-{index}",
            json.dumps({"phase": phase, "index": index}, ensure_ascii=True, sort_keys=True),
            json.dumps({"ok": True}, ensure_ascii=True, sort_keys=True),
            ts,
            ts,
            ts,
            ts,
        )
        inserted["workflow_runs"] += 1

    return inserted


def _restored_probe(restored_db: Path) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(restored_db),
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        readiness = client.get("/system/readiness")
        slo = client.get("/system/slo")
        integrity = client.get("/system/database/integrity?mode=quick")
        create_goal = client.post(
            "/goals",
            json={
                "title": "RTO/RPO restore probe goal",
                "description": "post-restore mutation probe",
                "urgency": 0.7,
                "value": 0.6,
                "deadline_score": 0.3,
            },
        )

    _expect(readiness.status_code == 200, f"Restored readiness failed: {readiness.text}")
    _expect(slo.status_code == 200, f"Restored SLO failed: {slo.text}")
    _expect(integrity.status_code == 200, f"Restored integrity failed: {integrity.text}")
    _expect(create_goal.status_code == 201, f"Restored mutation probe failed: {create_goal.status_code} {create_goal.text}")

    readiness_payload = readiness.json()
    slo_payload = slo.json()
    integrity_payload = integrity.json()

    _expect(bool(readiness_payload.get("ready")), f"Restored readiness is false: {readiness_payload}")
    _expect(str(slo_payload.get("status")) == "ok", f"Restored SLO not ok: {slo_payload}")
    _expect(
        bool((integrity_payload.get("integrity") or {}).get("ok")),
        f"Restored integrity endpoint not ok: {integrity_payload}",
    )

    return {
        "readiness_ready": bool(readiness_payload.get("ready")),
        "slo_status": str(slo_payload.get("status")),
        "integrity_ok": bool((integrity_payload.get("integrity") or {}).get("ok")),
        "post_restore_goal_status_code": int(create_goal.status_code),
        "post_restore_goal_id": str((create_goal.json() or {}).get("goal_id") or ""),
    }


def run_check(
    *,
    label: str,
    deployment_profile: str,
    workspace: Path,
    policy_file: Path,
    runbook_file: Path,
    seed_rows: int,
    tail_write_rows: int,
    max_rto_seconds: float,
    max_rpo_rows_lost: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = str(deployment_profile).strip().lower() or "production"

    policy = _read_json_file(policy_file)
    runbook_text = runbook_file.read_text(encoding="utf-8")

    policy_version = str(policy.get("version") or "")
    _expect(bool(policy_version), "Policy field 'version' is required.")

    scenario_defs = policy.get("scenarios") if isinstance(policy.get("scenarios"), list) else []
    _expect(len(scenario_defs) >= 2, "Policy must contain at least two scenarios.")

    raw_policy_max_rto = policy.get("max_rto_seconds")
    policy_max_rto_seconds = float(raw_policy_max_rto) if raw_policy_max_rto is not None else 0.0
    _expect(policy_max_rto_seconds > 0, "Policy field 'max_rto_seconds' must be > 0.")

    raw_policy_max_rpo = policy.get("max_rpo_rows_lost")
    policy_max_rpo_rows_lost = int(raw_policy_max_rpo) if raw_policy_max_rpo is not None else -1
    _expect(policy_max_rpo_rows_lost >= 0, "Policy field 'max_rpo_rows_lost' must be >= 0.")

    effective_max_rto_seconds = min(float(max_rto_seconds), policy_max_rto_seconds)
    effective_max_rpo_rows_lost = min(int(max_rpo_rows_lost), policy_max_rpo_rows_lost)

    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace / f"rto-rpo-assertion-suite-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)

    source_db = run_dir / "source.db"
    backup_zero = run_dir / "backup-zero-loss.db"
    restored_zero = run_dir / "restored-zero-loss.db"
    backup_bounded = run_dir / "backup-bounded-loss.db"
    restored_bounded = run_dir / "restored-bounded-loss.db"

    try:
        seeded_initial = _seed_rows(
            db_path=source_db,
            row_count=max(1, int(seed_rows)),
            phase="initial",
            start_index=0,
        )

        source_initial = _snapshot(source_db)
        _expect(
            bool(source_initial["integrity"]["quick_ok"]) and bool(source_initial["integrity"]["full_ok"]),
            f"Source DB integrity failed after initial seed: {source_initial['integrity']}",
        )

        _copy_database(source_db, backup_zero)
        zero_restore_started = time.perf_counter()
        _copy_database(backup_zero, restored_zero)
        zero_restore_duration_ms = int((time.perf_counter() - zero_restore_started) * 1000)
        zero_snapshot = _snapshot(restored_zero)
        zero_probe = _restored_probe(restored_zero)

        source_before_bounded_backup = _snapshot(source_db)
        _copy_database(source_db, backup_bounded)

        seeded_tail = _seed_rows(
            db_path=source_db,
            row_count=max(0, int(tail_write_rows)),
            phase="tail",
            start_index=max(1, int(seed_rows)),
        )
        source_after_tail = _snapshot(source_db)

        bounded_restore_started = time.perf_counter()
        _copy_database(backup_bounded, restored_bounded)
        bounded_restore_duration_ms = int((time.perf_counter() - bounded_restore_started) * 1000)
        bounded_snapshot = _snapshot(restored_bounded)
        bounded_probe = _restored_probe(restored_bounded)

        zero_rows_lost = max(0, int(source_initial["total_rows"]) - int(zero_snapshot["total_rows"]))
        bounded_rows_lost = max(0, int(source_after_tail["total_rows"]) - int(bounded_snapshot["total_rows"]))

        criteria: list[dict[str, Any]] = []

        def add(name: str, passed: bool, details: str) -> None:
            criteria.append({"name": name, "passed": bool(passed), "details": details})

        if profile == "production":
            add("policy_version_present", bool(policy_version), f"policy_version={policy_version!r}")

            for index, scenario in enumerate(scenario_defs):
                if not isinstance(scenario, dict):
                    add(f"scenario_{index + 1}.shape", False, "scenario entry is not an object")
                    continue
                scenario_id = str(scenario.get("id") or "").strip()
                runbook_section = str(scenario.get("runbook_section") or "").strip()
                add(
                    f"scenario_{index + 1}.id_present",
                    bool(scenario_id),
                    f"scenario_id={scenario_id!r}",
                )
                add(
                    f"{scenario_id or ('scenario_' + str(index + 1))}.runbook_section_present",
                    bool(runbook_section) and runbook_section in runbook_text,
                    f"runbook_section={runbook_section!r}",
                )

            add(
                "zero_loss_restore_matches_source",
                zero_snapshot["counts"] == source_initial["counts"],
                (
                    f"source_total_rows={source_initial['total_rows']}, "
                    f"restored_total_rows={zero_snapshot['total_rows']}"
                ),
            )
            add(
                "zero_loss_rows_lost_zero",
                zero_rows_lost == 0,
                f"zero_rows_lost={zero_rows_lost}",
            )
            add(
                "zero_loss_rto_budget",
                (zero_restore_duration_ms / 1000.0) <= float(effective_max_rto_seconds),
                (
                    f"zero_restore_duration_seconds={zero_restore_duration_ms / 1000.0:.3f}, "
                    f"max_rto_seconds={effective_max_rto_seconds:.3f}"
                ),
            )
            add(
                "zero_loss_integrity_ok",
                bool(zero_snapshot["integrity"]["quick_ok"]) and bool(zero_snapshot["integrity"]["full_ok"]),
                f"zero_integrity={json.dumps(zero_snapshot['integrity'], sort_keys=True)}",
            )
            add(
                "zero_loss_runtime_probe_ok",
                bool(zero_probe["readiness_ready"]) and str(zero_probe["slo_status"]) == "ok" and bool(zero_probe["integrity_ok"]),
                f"zero_probe={json.dumps(zero_probe, sort_keys=True)}",
            )

            add(
                "bounded_loss_restore_matches_backup_point",
                bounded_snapshot["counts"] == source_before_bounded_backup["counts"],
                (
                    f"backup_point_total_rows={source_before_bounded_backup['total_rows']}, "
                    f"restored_total_rows={bounded_snapshot['total_rows']}"
                ),
            )
            add(
                "bounded_loss_rto_budget",
                (bounded_restore_duration_ms / 1000.0) <= float(effective_max_rto_seconds),
                (
                    f"bounded_restore_duration_seconds={bounded_restore_duration_ms / 1000.0:.3f}, "
                    f"max_rto_seconds={effective_max_rto_seconds:.3f}"
                ),
            )
            add(
                "bounded_loss_rpo_budget",
                bounded_rows_lost <= int(effective_max_rpo_rows_lost),
                (
                    f"bounded_rows_lost={bounded_rows_lost}, "
                    f"max_rpo_rows_lost={effective_max_rpo_rows_lost}"
                ),
            )
            add(
                "bounded_loss_integrity_ok",
                bool(bounded_snapshot["integrity"]["quick_ok"]) and bool(bounded_snapshot["integrity"]["full_ok"]),
                f"bounded_integrity={json.dumps(bounded_snapshot['integrity'], sort_keys=True)}",
            )
            add(
                "bounded_loss_runtime_probe_ok",
                bool(bounded_probe["readiness_ready"]) and str(bounded_probe["slo_status"]) == "ok" and bool(bounded_probe["integrity_ok"]),
                f"bounded_probe={json.dumps(bounded_probe, sort_keys=True)}",
            )
        else:
            add("non_production_profile", True, f"deployment_profile={profile!r} (hard requirements skipped)")

        failed_criteria = [item for item in criteria if item["passed"] is False]
        success = len(failed_criteria) == 0

        report = {
            "label": label,
            "success": bool(success),
            "config": {
                "deployment_profile": profile,
                "policy_file": str(policy_file),
                "runbook_file": str(runbook_file),
                "seed_rows": max(1, int(seed_rows)),
                "tail_write_rows": max(0, int(tail_write_rows)),
                "max_rto_seconds": float(effective_max_rto_seconds),
                "max_rpo_rows_lost": int(effective_max_rpo_rows_lost),
            },
            "policy": policy,
            "metrics": {
                "criteria_total": len(criteria),
                "criteria_passed": len(criteria) - len(failed_criteria),
                "criteria_failed": len(failed_criteria),
                "zero_restore_duration_ms": int(zero_restore_duration_ms),
                "bounded_restore_duration_ms": int(bounded_restore_duration_ms),
                "max_restore_duration_ms": int(max(zero_restore_duration_ms, bounded_restore_duration_ms)),
                "zero_rows_lost": int(zero_rows_lost),
                "bounded_rows_lost": int(bounded_rows_lost),
                "seeded_initial_rows": int(source_initial["total_rows"]),
                "seeded_tail_rows": int(_total_rows(seeded_tail)),
                "source_total_rows_after_tail": int(source_after_tail["total_rows"]),
            },
            "seeded": {
                "initial": seeded_initial,
                "tail": seeded_tail,
            },
            "scenarios": {
                "zero_loss": {
                    "source": source_initial,
                    "restored": zero_snapshot,
                    "runtime_probe": zero_probe,
                    "restore_duration_ms": int(zero_restore_duration_ms),
                    "rows_lost": int(zero_rows_lost),
                },
                "bounded_loss": {
                    "backup_point_source": source_before_bounded_backup,
                    "source_after_tail": source_after_tail,
                    "restored": bounded_snapshot,
                    "runtime_probe": bounded_probe,
                    "restore_duration_ms": int(bounded_restore_duration_ms),
                    "rows_lost": int(bounded_rows_lost),
                },
            },
            "criteria": criteria,
            "failed_criteria": failed_criteria,
            "paths": {
                "run_dir": str(run_dir),
                "source_db": str(source_db),
                "backup_zero_db": str(backup_zero),
                "restored_zero_db": str(restored_zero),
                "backup_bounded_db": str(backup_bounded),
                "restored_bounded_db": str(restored_bounded),
            },
            "generated_at_utc": _utc_now(),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return report
    finally:
        if not keep_artifacts:
            import shutil

            shutil.rmtree(run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "RTO/RPO assertion suite: validate restore-time and data-loss budgets over zero-loss and bounded-loss scenarios."
        )
    )
    parser.add_argument("--label", default="rto-rpo-assertion-suite")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "rto-rpo-assertion-suite"))
    parser.add_argument("--policy-file", default="docs/rto-rpo-assertion-policy.json")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--seed-rows", type=int, default=48)
    parser.add_argument("--tail-write-rows", type=int, default=12)
    parser.add_argument("--max-rto-seconds", type=float, default=20.0)
    parser.add_argument("--max-rpo-rows-lost", type=int, default=96)
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]

    workspace = Path(str(args.workspace)).expanduser()
    if not workspace.is_absolute():
        workspace = (project_root / workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    policy_file = Path(str(args.policy_file)).expanduser()
    if not policy_file.is_absolute():
        policy_file = (project_root / policy_file).resolve()

    runbook_file = Path(str(args.runbook_file)).expanduser()
    if not runbook_file.is_absolute():
        runbook_file = (project_root / runbook_file).resolve()

    try:
        report = run_check(
            label=str(args.label),
            deployment_profile=str(args.deployment_profile),
            workspace=workspace,
            policy_file=policy_file,
            runbook_file=runbook_file,
            seed_rows=max(1, int(args.seed_rows)),
            tail_write_rows=max(0, int(args.tail_write_rows)),
            max_rto_seconds=max(0.1, float(args.max_rto_seconds)),
            max_rpo_rows_lost=max(0, int(args.max_rpo_rows_lost)),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[rto-rpo-assertion-suite] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[rto-rpo-assertion-suite] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
