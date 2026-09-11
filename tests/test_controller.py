from __future__ import annotations

import base64
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
    headers = {"Authorization": "Basic " + base64.b64encode(server.expected_credentials).decode(),
               "X-REXS-Request": "1"}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(Request(f"http://{host}:{port}/api/experiments", headers=headers), timeout=5) as response:
            payload = json.load(response)
        assert payload[0]["id"] == record.id
        assert payload[0]["status"] == "GENERATED"
        assert payload[0]["replica_count"] == 3
        assert "spec_text" not in payload[0]
        with urlopen(Request(f"http://{host}:{port}/api/experiments/{record.id}", headers=headers), timeout=5) as response:
            detail = json.load(response)
        assert detail["experiment"]["spec_text"] == "version: v2\ntasks: []\n"
        assert detail["spec"] == {"version": "v2", "tasks": []}
        with urlopen(Request(f"http://{host}:{port}/", headers=headers), timeout=5) as response:
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
    headers = {"Authorization": "Basic " + base64.b64encode(server.expected_credentials).decode(),
               "X-REXS-Request": "1"}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with urlopen(Request(f"http://{host}:{port}/api/experiments?status=RUNNING", headers=headers), timeout=5) as response:
            payload = json.load(response)
        assert [item["id"] for item in payload] == [record.id]

        request = Request(
            f"http://{host}:{port}/api/experiments/{record.id}/cancel",
            method="POST", headers=headers,
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


@pytest.mark.parametrize("state,exit_code", [("COMPLETED", "0:0"), ("FAILED", "1:0")])
def test_finished_job_reconciles_when_squeue_rejects_id(tmp_path, state, exit_code):
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    store.record_submission(record.id, "12345", "submitted")

    def runner(command, **kwargs):
        if command[0] == "squeue":
            raise subprocess.CalledProcessError(1, command, stderr="Invalid job id specified")
        return subprocess.CompletedProcess(command, 0, stdout=f"{state}|{exit_code}\n", stderr="")

    Controller(store.path, runner=runner).refresh()
    saved = store.get(record.id)
    assert saved.status == state
    assert saved.exit_code == exit_code
    assert saved.finished_at is not None


def test_one_failed_query_does_not_block_other_jobs(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    first = _record(store, tmp_path)
    second = _record(store, tmp_path)
    store.record_submission(first.id, "12345", "submitted")
    store.record_submission(second.id, "12346", "submitted")

    def runner(command, **kwargs):
        if "12345" in command:
            raise subprocess.CalledProcessError(1, command, stderr="Slurm unavailable")
        return subprocess.CompletedProcess(command, 0, stdout="RUNNING\n", stderr="")

    controller = Controller(store.path, runner=runner)
    updates = controller.refresh()
    assert [update.job_id for update in updates] == ["12346"]
    assert store.get(first.id).status == "SUBMITTED"
    assert store.get(second.id).status == "RUNNING"
    with pytest.raises(subprocess.CalledProcessError):
        controller.refresh(first.id)


def test_runtime_backfills_finished_jobs_and_persists(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    store.record_submission(record.id, "12345", "submitted")
    store.update_status(record.id, "COMPLETED", exit_code="0:0")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert "--format=JobIDRaw,ElapsedRaw" in command
        return subprocess.CompletedProcess(command, 0, stdout="12345|204\n12345.batch|999\n", stderr="")

    controller = Controller(store.path, runner=runner)
    controller.refresh()
    assert StateStore(store.path).get(record.id).runtime_seconds == 204
    controller.refresh()
    assert len(calls) == 1


def test_start_estimates_use_utc_and_clear_unavailable_values(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    record = _record(store, tmp_path)
    store.record_submission(record.id, "12345", "submitted")
    outputs = iter(["12345|2026-09-10T06:38:07\n", "12345|N/A\n"])

    def runner(command, **kwargs):
        assert "--start" in command
        assert kwargs["env"]["TZ"] == "UTC"
        return subprocess.CompletedProcess(command, 0, stdout=next(outputs), stderr="")

    controller = Controller(store.path, runner=runner)
    controller.refresh_start_estimates()
    assert store.get(record.id).estimated_start_at == "2026-09-10T06:38:07+00:00"
    controller.refresh_start_estimates()
    assert store.get(record.id).estimated_start_at is None


def test_experiments_defaults_to_active_before_limit(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    store = StateStore(db)
    for index, status in enumerate(("SUBMITTED", "PENDING", "RUNNING", "FAILED", "CANCELLED", "COMPLETED")):
        record = _record(store, tmp_path)
        with store.connect() as connection:
            connection.execute(
                "UPDATE experiments SET status = ?, created_at = ? WHERE id = ?",
                (status, f"2026-01-01T00:00:0{index}", record.id),
            )
    cli = Rexs()
    options = {"db": str(db), "refresh": False, "details": True}
    assert [item["status"] for item in cli.experiments(limit=2, **options)] == ["RUNNING", "PENDING"]
    assert len(cli.experiments(all=True, **options)) == 6
    assert [item["status"] for item in cli.experiments(status="failed", **options)] == ["FAILED"]
