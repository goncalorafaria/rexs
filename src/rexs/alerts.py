"""Opt-in, passive alerts. No delivery hooks or workflow dependencies."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from rexs.errors import ConfigurationError

FAILURES = {"FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "SUBMISSION_FAILED"}


def validate_alerts(settings, task_names):
    if not isinstance(settings, dict) or set(settings) - {"experiment", "tasks"}:
        raise ConfigurationError("alerts supports experiment and tasks")
    tasks = settings.get("tasks", {})
    if not isinstance(tasks, dict) or set(tasks) - set(task_names):
        raise ConfigurationError("alerts.tasks must map existing task names to alert settings")
    for rule in [settings.get("experiment", {}), *tasks.values()]:
        if not isinstance(rule, dict) or set(rule) - {"completed", "failed", "failure_reasons"}:
            raise ConfigurationError("Alert settings: completed, failed, failure_reasons")
        if any(not isinstance(rule.get(key, False), bool) for key in ("completed", "failed")):
            raise ConfigurationError("Alert completed/failed flags must be booleans")
        reasons = rule.get("failure_reasons", [])
        if not isinstance(reasons, list) or any(not isinstance(r, str) or r not in FAILURES for r in reasons):
            raise ConfigurationError("failure_reasons must list supported terminal failure states")
        if "failure_reasons" in rule and not rule.get("failed", False):
            raise ConfigurationError("failure_reasons requires failed: true")


def record_alerts(db, experiment, spec, status, *, allocation=None):
    """Called in the same transaction as the observed terminal state."""
    settings = spec.get("rexs", {}).get("alerts", {})
    if not settings or status not in FAILURES | {"COMPLETED"}:
        return
    kind = "completed" if status == "COMPLETED" else "failed"
    if allocation:
        bindings = json.loads(allocation["tasks_json"])
        subjects = [
            (name, [t["rank"] for t in bindings if t["name"] == name], rule)
            for name, rule in settings.get("tasks", {}).items()
            if any(t["name"] == name for t in bindings)
        ]
        job_id = allocation["job_id"]
        attempt = db.execute(
            "SELECT COUNT(*) FROM allocation_attempts WHERE experiment_id=? AND name=?",
            (experiment["id"], allocation["name"]),
        ).fetchone()[0]
    else:
        subjects = [("", [], settings.get("experiment", {}))]
        job_id = experiment["job_id"]
        attempt = 0
    for task, ranks, rule in subjects:
        if not rule.get(kind, False):
            continue
        if kind == "failed" and status not in rule.get("failure_reasons", FAILURES):
            continue
        allocation_name = allocation["name"] if allocation else ""
        key = json.dumps([experiment["id"], allocation_name, task, job_id, attempt, kind])
        context = {
            "schema_version": 1,
            "experiment_name": experiment["name"],
            "replica_ranks": ranks,
            "attempt": attempt,
            "exit_code": allocation["exit_code"] if allocation else experiment["exit_code"],
            "run_root": allocation["run_root"] if allocation else experiment["run_root"],
            "dashboard_path": "/experiment/" + experiment["id"],
            # Scheduler status describes the allocation, not a diagnosis of an individual child.
            "status_source": "allocation" if allocation else "experiment",
        }
        if allocation:
            context["failure_policy"] = (
                spec.get("rexs", {}).get("failure_policies", {}).get(task, {"action": "continue"})
            )
        elif kind == "failed":
            context["failed_allocations"] = [
                {"name": row["name"], "job_id": row["job_id"], "reason": row["status"], "exit_code": row["exit_code"]}
                for row in db.execute(
                    "SELECT name,job_id,status,exit_code FROM allocations WHERE experiment_id=?", (experiment["id"],)
                )
                if row["status"] in FAILURES
            ]
        db.execute(
            """INSERT OR IGNORE INTO alerts
            (dedupe_key, created_at, experiment_id, allocation_name, task_name, job_id, kind, reason, context_json)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                key,
                datetime.now(UTC).isoformat(),
                experiment["id"],
                allocation_name,
                task,
                job_id,
                kind,
                status,
                json.dumps(context),
            ),
        )
