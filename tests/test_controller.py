from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from rexs.cli import Rexs, _definitions
from rexs.controller import Controller
from rexs.server import RexsServer
from rexs.state import StateStore


def _record(store: StateStore, tmp_path: Path):
    spec = tmp_path / "experiment.yaml"
    script = tmp_path / "experiment.sbatch"
    spec.write_text("version: v2\ntasks: []\n", encoding="utf-8")
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    return store.create_experiment(
        name="test-run",
        spec_text=spec.read_text(encoding="utf-8"),
        spec_path=str(spec),
        spec_sha256="spec-hash",
        script_path=str(script),
        script_sha256="script-hash",
        run_root=str(tmp_path / "runs"),
        warnings=("one warning",),
        tasks=(
            {"name": "worker", "replicas": 2},
            {"name": "service"},
        ),
    )


def test_fire_define_accepts_yaml_mapping() -> None:
    assert _definitions("{CLUSTER: ai2/holmes, VLLM_REPLICAS: 2}") == {
        "CLUSTER": "ai2/holmes",
        "VLLM_REPLICAS": "2",
    }


def test_state_store_migrates_database_without_spec_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "old-state.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            CREATE TABLE experiments (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                job_id TEXT UNIQUE, spec_path TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
                script_path TEXT NOT NULL, script_sha256 TEXT NOT NULL,
                run_root TEXT NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]',
                submission_output TEXT, exit_code TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, submitted_at TEXT, finished_at TEXT
            )
            """
        )

    StateStore(db)

    with sqlite3.connect(db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(experiments)")}
    assert "spec_text" in columns


def test_sqlite_store_tracks_submission_tasks_and_events(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    submitted = store.record_submission(record.id, "12345", "Submitted batch job 12345")

    assert submitted.status == "SUBMITTED"
    assert submitted.job_id == "12345"
    assert store.get("12345").id == record.id
    assert [(task.name, task.replica_rank) for task in store.tasks(record.id)] == [
        ("service", 0),
        ("worker", 0),
        ("worker", 1),
    ]
    assert [event["status"] for event in store.events(record.id)] == ["GENERATED", "SUBMITTED"]


def test_controller_refreshes_terminal_state_from_sacct(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    store.record_submission(record.id, "12345", "Submitted batch job 12345")

    def runner(command, **kwargs):
        del kwargs
        if command[0] == "squeue":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="COMPLETED|0:0\n", stderr="")

    controller = Controller(store.path, runner=runner)
    updates = controller.refresh(record.id)

    assert updates[0].status == "COMPLETED"
    assert updates[0].exit_code == "0:0"
    assert store.get(record.id).finished_at is not None


def test_controller_reads_per_replica_logs(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    store.record_submission(record.id, "12345", "Submitted batch job 12345")
    log = tmp_path / "runs" / "12345" / "logs" / "worker.1.log"
    log.parent.mkdir(parents=True)
    log.write_text("first\nsecond\nthird\n", encoding="utf-8")

    output = Controller(store.path).logs(record.id, task="worker", replica=1, lines=2)

    assert output[0]["exists"] is True
    assert output[0]["content"] == "second\nthird"


def test_http_api_lists_tracked_experiments(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    server = RexsServer(("127.0.0.1", 0), Controller(store.path), poll_interval=60)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/api/experiments", timeout=5) as response:
            payload = json.load(response)
        assert payload[0]["id"] == record.id
        assert payload[0]["status"] == "GENERATED"
        assert "spec_text" not in payload[0]
        with urlopen(f"http://{host}:{port}/api/experiments/{record.id}", timeout=5) as response:
            detail = json.load(response)
        assert detail["experiment"]["spec_text"] == "version: v2\ntasks: []\n"
        assert detail["spec"] == {"version": "v2", "tasks": []}
        with urlopen(f"http://{host}:{port}/", timeout=5) as response:
            dashboard = response.read().decode()
        assert "Experiment history" in dashboard
        assert "Cancel experiment" in dashboard
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_api_filters_and_cancels_experiments(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    store.record_submission(record.id, "12345", "Submitted batch job 12345")
    store.update_status(record.id, "RUNNING")
    commands = []

    def runner(command, **kwargs):
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    server = RexsServer(("127.0.0.1", 0), Controller(store.path, runner=runner), poll_interval=60)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/api/experiments?status=RUNNING", timeout=5) as response:
            payload = json.load(response)
        assert [item["id"] for item in payload] == [record.id]

        request = Request(
            f"http://{host}:{port}/api/experiments/{record.id}/cancel",
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            cancelled = json.load(response)
        assert cancelled["status"] == "CANCELLED"
        assert commands == [["scancel", "12345"]]
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 409
        assert commands == [["scancel", "12345"]]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_fire_submit_persists_before_returning_job(monkeypatch, tmp_path: Path) -> None:
    spec = tmp_path / "job.yaml"
    spec.write_text(
        """\
version: v2
tasks:
  - name: worker
    image: {docker: busybox:latest}
    command: [echo, hello]
""",
        encoding="utf-8",
    )
    db = tmp_path / "state.sqlite3"

    def submit(command, **kwargs):
        del kwargs
        assert command[0] == "sbatch"
        return subprocess.CompletedProcess(command, 0, stdout="Submitted batch job 9876\n", stderr="")

    monkeypatch.setattr("rexs.cli.subprocess.run", submit)
    result = Rexs().submit(str(spec), db=str(db))

    assert result["job_id"] == "9876"
    assert result["status"] == "SUBMITTED"
    stored = StateStore(db).get("9876")
    assert Path(stored.spec_path) == spec
    assert stored.spec_text == spec.read_text(encoding="utf-8")
    assert Path(stored.script_path).is_file()
    assert [event["status"] for event in StateStore(db).events("9876")] == ["GENERATED", "SUBMITTED"]
