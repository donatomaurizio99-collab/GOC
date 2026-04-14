from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_drill(*, goal_count: int) -> dict:
    app = create_app(Settings(database_url=":memory:"))

    with TestClient(app) as client:
        counters = {
            "goals_created": 0,
            "tasks_created": 0,
            "tasks_succeeded": 0,
            "tasks_failed": 0,
            "goals_archived": 0,
        }

        for index in range(int(goal_count)):
            created = client.post(
                "/goals",
                json={
                    "title": f"Invariant burst goal {index}",
                    "description": "queue/goal/task invariant burst drill",
                    "urgency": 0.5,
                    "value": 0.6,
                    "deadline_score": 0.3,
                },
            )
            _expect(created.status_code == 201, f"Goal create failed at index={index}: {created.text}")
            goal_id = str(created.json()["goal_id"])
            counters["goals_created"] += 1

            if index % 10 == 0:
                age = client.post("/system/scheduler/age")
                _expect(age.status_code == 200, f"Scheduler age failed at index={index}: {age.text}")
                pick = client.post("/system/scheduler/pick")
                _expect(pick.status_code == 200, f"Scheduler pick failed at index={index}: {pick.text}")

            activate = client.post(f"/goals/{goal_id}/activate")
            if activate.status_code == 409:
                goal = client.get(f"/goals/{goal_id}")
                _expect(goal.status_code == 200, f"Goal fetch failed for {goal_id}: {goal.text}")
                _expect(
                    str(goal.json().get("state")) == "active",
                    f"Goal activate conflict did not end in active state for {goal_id}: {activate.text}",
                )
            else:
                _expect(activate.status_code == 200, f"Goal activate failed for {goal_id}: {activate.text}")

            task = client.post(
                "/tasks",
                json={"goal_id": goal_id, "title": f"Invariant burst task {index}"},
            )
            _expect(task.status_code == 201, f"Task create failed for goal {goal_id}: {task.text}")
            task_id = str(task.json()["task_id"])
            counters["tasks_created"] += 1

            scenario = index % 3
            if scenario == 0:
                success = client.post(f"/tasks/{task_id}/success")
                _expect(success.status_code == 200, f"Task success failed for {task_id}: {success.text}")
                counters["tasks_succeeded"] += 1
                block = client.post(f"/goals/{goal_id}/block")
                _expect(block.status_code == 200, f"Goal block failed for {goal_id}: {block.text}")
                archive = client.post(f"/goals/{goal_id}/archive")
                _expect(archive.status_code == 200, f"Goal archive failed for {goal_id}: {archive.text}")
                counters["goals_archived"] += 1
                continue

            if scenario == 1:
                for attempt in range(2):
                    failed = client.post(
                        f"/tasks/{task_id}/fail",
                        json={
                            "failure_type": "SkillFailure",
                            "error_message": f"invariant burst poison attempt {attempt}",
                        },
                    )
                    _expect(
                        failed.status_code == 200,
                        f"Skill failure attempt {attempt} failed for {task_id}: {failed.text}",
                    )
                    counters["tasks_failed"] += 1

                approve = client.post(f"/goals/{goal_id}/hitl_approve")
                _expect(approve.status_code == 200, f"HITL approve failed for {goal_id}: {approve.text}")
                block = client.post(f"/goals/{goal_id}/block")
                _expect(block.status_code == 200, f"Goal block failed for {goal_id}: {block.text}")
                archive = client.post(f"/goals/{goal_id}/archive")
                _expect(archive.status_code == 200, f"Goal archive failed for {goal_id}: {archive.text}")
                counters["goals_archived"] += 1
                continue

            for attempt in range(3):
                failed = client.post(
                    f"/tasks/{task_id}/fail",
                    json={
                        "failure_type": "ExecutionFailure",
                        "error_message": f"invariant burst exhausted attempt {attempt}",
                    },
                )
                _expect(
                    failed.status_code == 200,
                    f"Execution failure attempt {attempt} failed for {task_id}: {failed.text}",
                )
                counters["tasks_failed"] += 1
            archive = client.post(f"/goals/{goal_id}/archive")
            _expect(archive.status_code == 200, f"Goal archive failed for {goal_id}: {archive.text}")
            counters["goals_archived"] += 1

        health = client.get("/system/health")
        _expect(health.status_code == 200, f"System health failed: {health.text}")
        health_payload = health.json()
        health_violations = list(health_payload.get("invariant_violations") or [])

        direct_violations = client.app.state.services.state_manager.find_invariant_violations()
        _expect(
            not direct_violations and not health_violations,
            (
                "Invariant violations detected. "
                f"direct={json.dumps(direct_violations, sort_keys=True)} "
                f"health={json.dumps(health_violations, sort_keys=True)}"
            ),
        )

        queue_size = int(client.app.state.services.db.fetch_scalar("SELECT COUNT(*) FROM goal_queue") or 0)
        readiness = client.get("/system/readiness").json()
        _expect(bool(readiness["ready"]), f"Readiness is false: {json.dumps(readiness, sort_keys=True)}")

        return {
            "success": True,
            "goal_count": int(goal_count),
            "counters": counters,
            "queue_size": queue_size,
            "invariant_violations": {
                "direct": direct_violations,
                "health": health_violations,
            },
            "readiness_ready": bool(readiness["ready"]),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Burst drill for queue/goal/task consistency: execute mixed transition paths and verify "
            "no invariant violations."
        )
    )
    parser.add_argument("--goal-count", type=int, default=45)
    args = parser.parse_args(argv)

    if int(args.goal_count) <= 0:
        print("[invariant-burst-drill] ERROR: --goal-count must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(goal_count=int(args.goal_count))
    except Exception as exc:
        print(f"[invariant-burst-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
