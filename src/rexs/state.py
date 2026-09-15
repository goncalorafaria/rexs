from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from rexs.alerts import record_alerts

TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "SUBMISSION_FAILED",
}


def default_db_path() -> Path:
    override = os.environ.get("REXS_STATE_DB")
    if override:
        return Path(override).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "rexs" / "state.sqlite3"


@dataclass(frozen=True)
class ExperimentRecord:
    id: str
    name: str
    status: str
    job_id: str | None
    spec_path: str
    spec_sha256: str
    spec_text: str
    script_path: str
    script_sha256: str
    run_root: str
    warnings: tuple[str, ...]
    submission_output: str | None
    exit_code: str | None
    created_at: str
    updated_at: str
    submitted_at: str | None
    finished_at: str | None
    runtime_seconds: int | None = None
    estimated_start_at: str | None = None

    allocations: tuple[dict[str, Any], ...] = ()

    def as_dict(self, *, include_spec: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not include_spec:
            value.pop("spec_text")
            for allocation in value["allocations"]:
                allocation.pop("spec_text", None)
        return value


@dataclass(frozen=True)
class TaskRecord:
    experiment_id: str
    name: str
    replica_rank: int
    log_path: str | None
    result_path: str | None
    allocation: str | None = None
    job_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateStore:
    """SQLite-backed source of truth for REXS experiments."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            # Respect the database's journal mode. Changing it on every request
            # requires an exclusive lock and can conflict with other clients.
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create_experiment(
        self,
        *,
        name: str,
        spec_path: str,
        spec_text: str,
        spec_sha256: str,
        script_path: str,
        script_sha256: str,
        run_root: str,
        warnings: Sequence[str],
        tasks: Sequence[Mapping[str, Any]],
    ) -> ExperimentRecord:
        experiment_id = uuid4().hex
        now = _now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO experiments (
                    id, name, status, spec_path, spec_sha256, spec_text, script_path,
                    script_sha256, run_root, warnings_json, created_at, updated_at
                ) VALUES (?, ?, 'GENERATED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    name,
                    spec_path,
                    spec_sha256,
                    spec_text,
                    script_path,
                    script_sha256,
                    run_root,
                    json.dumps(list(warnings)),
                    now,
                    now,
                ),
            )
            for task in tasks:
                task_name = str(task.get("name") or "task")
                replicas = int(task.get("replicas", 1))
                for rank in range(replicas):
                    connection.execute(
                        """
                        INSERT INTO task_replicas (experiment_id, name, replica_rank)
                        VALUES (?, ?, ?)
                        """,
                        (experiment_id, task_name, rank),
                    )
            self._event(connection, experiment_id, None, "GENERATED", "experiment compiled")
        return self.get(experiment_id)

    def record_submission(self, experiment_id: str, job_id: str, output: str) -> ExperimentRecord:
        now = _now()
        with self.connect() as connection:
            previous = self._status(connection, experiment_id)
            connection.execute(
                """
                UPDATE experiments
                SET job_id = ?, status = 'SUBMITTED', submission_output = ?,
                    submitted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (job_id, output, now, now, experiment_id),
            )
            self._event(connection, experiment_id, previous, "SUBMITTED", output.strip())
        return self.get(experiment_id)

    def record_submission_failure(self, experiment_id: str, detail: str) -> ExperimentRecord:
        return self.update_status(experiment_id, "SUBMISSION_FAILED", detail=detail)

    def update_status(
        self,
        experiment_id: str,
        status: str,
        *,
        detail: str | None = None,
        exit_code: str | None = None,
    ) -> ExperimentRecord:
        now = _now()
        finished_at = now if status in TERMINAL_STATES else None
        with self.connect() as connection:
            previous = self._status(connection, experiment_id)
            if previous == status and exit_code is None:
                return self.get(experiment_id)
            connection.execute(
                """
                UPDATE experiments
                SET status = ?, exit_code = COALESCE(?, exit_code),
                    finished_at = COALESCE(?, finished_at), updated_at = ?
                WHERE id = ?
                """,
                (status, exit_code, finished_at, now, experiment_id),
            )
            self._event(connection, experiment_id, previous, status, detail)
            row = connection.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
            record_alerts(connection, row, yaml.safe_load(row["spec_text"]) or {}, status)
        return self.get(experiment_id)

    def get(self, identifier: str) -> ExperimentRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE id = ? OR job_id = ? OR id IN (SELECT experiment_id FROM allocations WHERE job_id = ?) ORDER BY created_at DESC LIMIT 1",
                (identifier, identifier, identifier),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown experiment or Slurm job: {identifier}")
        return self._record(row)

    def list(self, *, status: str | Sequence[str] | None = None, limit: int = 100) -> list[ExperimentRecord]:
        query = "SELECT * FROM experiments"
        values: list[Any] = []
        if status:
            statuses = [status] if isinstance(status, str) else status
            query += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            values.extend(item.upper() for item in statuses)
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._record(row) for row in rows]

    def active(self) -> list[ExperimentRecord]:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM experiments
                WHERE (job_id IS NOT NULL OR EXISTS (SELECT 1 FROM allocations WHERE experiment_id = experiments.id)) AND status NOT IN ({placeholders})
                ORDER BY created_at
                """,
                tuple(TERMINAL_STATES),
            ).fetchall()
        return [self._record(row) for row in rows]

    def tasks(self, identifier: str) -> list[TaskRecord]:
        experiment = self.get(identifier)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT experiment_id, name, replica_rank
                FROM task_replicas
                WHERE experiment_id = ?
                ORDER BY name, replica_rank
                """,
                (experiment.id,),
            ).fetchall()
        result: list[TaskRecord] = []
        bindings = {}
        for allocation in experiment.allocations:
            for binding in allocation["tasks"]:
                bindings[(binding["name"], binding["rank"])] = (allocation, binding["local_rank"])
        for row in rows:
            log_path = None
            result_path = None
            allocation, rank = bindings.get((row["name"], row["replica_rank"]), ({}, row["replica_rank"]))
            job_id = allocation.get("job_id", experiment.job_id)
            if job_id:
                run_dir = Path(allocation.get("run_root", experiment.run_root)) / job_id
                log_path = str(run_dir / "logs" / f"{row['name']}.{rank}.log")
                result_path = str(run_dir / "results" / row["name"] / str(rank))
            result.append(
                TaskRecord(
                    experiment_id=experiment.id,
                    name=row["name"],
                    replica_rank=row["replica_rank"],
                    log_path=log_path,
                    result_path=result_path,
                    allocation=allocation.get("name"),
                    job_id=job_id,
                )
            )
        return result

    def _record(self, row):
        return replace(_experiment(row), allocations=tuple(self.allocations(row["id"])))

    def allocations(self, experiment_id):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM allocations WHERE experiment_id=? ORDER BY ordinal", (experiment_id,)
            ).fetchall()
        with self.connect() as db:
            parent = db.execute("SELECT spec_text FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        policies = (
            (yaml.safe_load(parent["spec_text"]) or {}).get("rexs", {}).get("failure_policies", {}) if parent else {}
        )
        result = []
        for row in rows:
            item = dict(row)
            item["tasks"] = json.loads(item.pop("tasks_json"))
            item["failure_policy"] = policies.get(item["tasks"][0]["name"], {"action": "continue"})
            with self.connect() as db:
                item["attempts"] = [
                    dict(r)
                    for r in db.execute(
                        "SELECT attempt,job_id,status,exit_code FROM allocation_attempts WHERE experiment_id=? AND name=? ORDER BY attempt",
                        (experiment_id, item["name"]),
                    )
                ]
            result.append(item)
        return result

    def add_allocation(
        self, experiment_id, *, name, ordinal, spec_text, script_path, script_sha256, run_root, tasks, completion=False
    ):
        with self.connect() as db:
            db.execute(
                "INSERT INTO allocations (experiment_id,name,ordinal,status,spec_text,script_path,script_sha256,run_root,tasks_json,completion) VALUES (?,?,?,'GENERATED',?,?,?,?,?,?)",
                (
                    experiment_id,
                    name,
                    ordinal,
                    spec_text,
                    script_path,
                    script_sha256,
                    run_root,
                    json.dumps(tasks),
                    int(completion),
                ),
            )

    def update_allocation(
        self,
        experiment_id,
        name,
        status,
        *,
        job_id=None,
        exit_code=None,
        script_path=None,
        script_sha256=None,
        detail=None,
    ):
        with self.connect() as db:
            db.execute(
                "UPDATE allocations SET status=?, job_id=COALESCE(?,job_id),exit_code=COALESCE(?,exit_code),script_path=COALESCE(?,script_path),script_sha256=COALESCE(?,script_sha256) WHERE experiment_id=? AND name=?",
                (status, job_id, exit_code, script_path, script_sha256, experiment_id, name),
            )
            self._event(db, experiment_id, None, status, f"Allocation {name}: {detail or status}")
            row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
            allocation = db.execute("SELECT * FROM allocations WHERE experiment_id=? AND name=?", (experiment_id, name)).fetchone()
            record_alerts(db, row, yaml.safe_load(row["spec_text"]) or {}, status, allocation=allocation)

    def alerts(self, *, after=0, limit=100, experiment_id=None):
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("after must be a nonnegative alert ID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        query = "SELECT * FROM alerts WHERE id > ?"
        params = [after]
        if experiment_id:
            query += " AND experiment_id=?"
            params.append(self.get(experiment_id).id)
        query += " ORDER BY id LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("dedupe_key")
            item["context"] = json.loads(item.pop("context_json"))
            result.append(item)
        return result

    def events(self, identifier: str) -> list[dict[str, Any]]:
        experiment = self.get(identifier)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT occurred_at, previous_status, status, detail
                FROM events WHERE experiment_id = ? ORDER BY sequence
                """,
                (experiment.id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remember_wandb_links(self, identifier: str, urls: Sequence[str]) -> list[str]:
        experiment = self.get(identifier)
        with self.connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO experiment_wandb_links(experiment_id, url) VALUES (?, ?)",
                [(experiment.id, url) for url in urls],
            )
            rows = connection.execute(
                "SELECT url FROM experiment_wandb_links WHERE experiment_id = ? ORDER BY url",
                (experiment.id,),
            ).fetchall()
        return [row["url"] for row in rows]

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    job_id TEXT UNIQUE,
                    spec_path TEXT NOT NULL,
                    spec_sha256 TEXT NOT NULL,
                    spec_text TEXT NOT NULL DEFAULT '',
                    script_path TEXT NOT NULL,
                    script_sha256 TEXT NOT NULL,
                    run_root TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    submission_output TEXT,
                    exit_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS allocations (
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL,
                    job_id TEXT UNIQUE, exit_code TEXT, spec_text TEXT NOT NULL,
                    script_path TEXT NOT NULL, script_sha256 TEXT NOT NULL,
                    run_root TEXT NOT NULL, tasks_json TEXT NOT NULL, completion INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(experiment_id,name)
                );
                CREATE TABLE IF NOT EXISTS allocation_attempts (
                    experiment_id TEXT NOT NULL, name TEXT NOT NULL, attempt INTEGER NOT NULL,
                    job_id TEXT NOT NULL, status TEXT NOT NULL, exit_code TEXT,
                    PRIMARY KEY(experiment_id,name,attempt)
                );
                CREATE TABLE IF NOT EXISTS task_replicas (
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    replica_rank INTEGER NOT NULL,
                    PRIMARY KEY (experiment_id, name, replica_rank)
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    allocation_name TEXT NOT NULL DEFAULT '', task_name TEXT NOT NULL DEFAULT '',
                    job_id TEXT, kind TEXT NOT NULL, reason TEXT NOT NULL, context_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS alerts_experiment_idx ON alerts(experiment_id, id);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    occurred_at TEXT NOT NULL,
                    previous_status TEXT,
                    status TEXT NOT NULL,
                    detail TEXT
                );

                CREATE TABLE IF NOT EXISTS experiment_wandb_links (
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    PRIMARY KEY (experiment_id, url)
                );

                CREATE TABLE IF NOT EXISTS wandb_sources (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    path TEXT NOT NULL, label TEXT NOT NULL, offset INTEGER NOT NULL DEFAULT 7,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS wandb_sources_experiment_idx ON wandb_sources(experiment_id);
                CREATE TABLE IF NOT EXISTS wandb_records (
                    source_id TEXT NOT NULL REFERENCES wandb_sources(id) ON DELETE CASCADE,
                    offset INTEGER NOT NULL, kind TEXT NOT NULL, timestamp REAL, payload TEXT NOT NULL,
                    PRIMARY KEY(source_id, offset)
                );
                CREATE TABLE IF NOT EXISTS wandb_gpu_latest (
                    source_id TEXT NOT NULL REFERENCES wandb_sources(id) ON DELETE CASCADE,
                    gpu TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL, timestamp REAL NOT NULL,
                    PRIMARY KEY(source_id,gpu,metric)
                );

                CREATE INDEX IF NOT EXISTS experiments_status_idx
                    ON experiments(status, created_at);
                CREATE INDEX IF NOT EXISTS events_experiment_idx
                    ON events(experiment_id, sequence);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(experiments)")}
            if "estimated_start_at" not in columns:
                connection.execute("ALTER TABLE experiments ADD COLUMN estimated_start_at TEXT")
            if "runtime_seconds" not in columns:
                connection.execute("ALTER TABLE experiments ADD COLUMN runtime_seconds INTEGER")
            if "spec_text" not in columns:
                connection.execute("ALTER TABLE experiments ADD COLUMN spec_text TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _status(connection: sqlite3.Connection, experiment_id: str) -> str:
        row = connection.execute("SELECT status FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown experiment: {experiment_id}")
        return str(row["status"])

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        experiment_id: str,
        previous: str | None,
        status: str,
        detail: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events (experiment_id, occurred_at, previous_status, status, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (experiment_id, _now(), previous, status, detail),
        )


def _experiment(row: sqlite3.Row) -> ExperimentRecord:
    return ExperimentRecord(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        job_id=row["job_id"],
        spec_path=row["spec_path"],
        spec_sha256=row["spec_sha256"],
        spec_text=row["spec_text"],
        script_path=row["script_path"],
        script_sha256=row["script_sha256"],
        run_root=row["run_root"],
        warnings=tuple(json.loads(row["warnings_json"])),
        submission_output=row["submission_output"],
        exit_code=row["exit_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        submitted_at=row["submitted_at"],
        finished_at=row["finished_at"],
        runtime_seconds=row["runtime_seconds"],
        estimated_start_at=row["estimated_start_at"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
