from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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

    def as_dict(self, *, include_spec: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not include_spec:
            value.pop("spec_text")
        return value


@dataclass(frozen=True)
class TaskRecord:
    experiment_id: str
    name: str
    replica_rank: int
    log_path: str | None
    result_path: str | None

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
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
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
        return self.get(experiment_id)

    def get(self, identifier: str) -> ExperimentRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE id = ? OR job_id = ? ORDER BY created_at DESC LIMIT 1",
                (identifier, identifier),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown experiment or Slurm job: {identifier}")
        return _experiment(row)

    def list(self, *, status: str | None = None, limit: int = 100) -> list[ExperimentRecord]:
        query = "SELECT * FROM experiments"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status.upper())
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_experiment(row) for row in rows]

    def active(self) -> list[ExperimentRecord]:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM experiments
                WHERE job_id IS NOT NULL AND status NOT IN ({placeholders})
                ORDER BY created_at
                """,
                tuple(TERMINAL_STATES),
            ).fetchall()
        return [_experiment(row) for row in rows]

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
        for row in rows:
            log_path = None
            result_path = None
            if experiment.job_id:
                run_dir = Path(experiment.run_root) / experiment.job_id
                log_path = str(run_dir / "logs" / f"{row['name']}.{row['replica_rank']}.log")
                result_path = str(run_dir / "results" / row["name"] / str(row["replica_rank"]))
            result.append(
                TaskRecord(
                    experiment_id=experiment.id,
                    name=row["name"],
                    replica_rank=row["replica_rank"],
                    log_path=log_path,
                    result_path=result_path,
                )
            )
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

                CREATE TABLE IF NOT EXISTS task_replicas (
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    replica_rank INTEGER NOT NULL,
                    PRIMARY KEY (experiment_id, name, replica_rank)
                );

                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    occurred_at TEXT NOT NULL,
                    previous_status TEXT,
                    status TEXT NOT NULL,
                    detail TEXT
                );

                CREATE INDEX IF NOT EXISTS experiments_status_idx
                    ON experiments(status, created_at);
                CREATE INDEX IF NOT EXISTS events_experiment_idx
                    ON events(experiment_id, sequence);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(experiments)")}
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
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
