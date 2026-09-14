import json
import subprocess
from pathlib import Path

import pytest
import yaml

from rexs.cli import Rexs
from rexs.controller import Controller
from rexs.errors import ConfigurationError, RexsError
from rexs.resources import resource_summary
from rexs.state import StateStore


@pytest.fixture
def bundle(tmp_path):
    profile = {"images": {"runtime": "/tmp/runtime.sif"}, "run_root": str(tmp_path / "runs")}
    (tmp_path / "profile.yaml").write_text(yaml.safe_dump(profile))
    spec = {
        "version": "v2",
        "tasks": [
            {"name": "redis", "image": {"beaker": "runtime"}, "command": ["true"]},
            {
                "name": "policy",
                "replicas": 2,
                "image": {"beaker": "runtime"},
                "command": ["true"],
                "envVars": [{"name": "CPU_JOB", "value": "${jobs.cpu}"}],
            },
            {"name": "evaluation", "image": {"beaker": "runtime"}, "command": ["true"]},
        ],
        "rexs": {
            "allocations": [
                {"name": "cpu", "tasks": ["redis"], "profile": "profile.yaml"},
                {"name": "policy", "tasks": ["policy"], "profile": "profile.yaml", "independent_replicas": True},
                {"name": "eval", "tasks": ["evaluation"], "profile": "profile.yaml"},
            ],
            "completion_task": "evaluation",
        },
    }
    source = tmp_path / "experiment.yaml"
    source.write_text(yaml.safe_dump(spec))
    return source, spec, str(tmp_path / "state.sqlite3")


def fake_submit(monkeypatch, *, fail_at=None):
    real_run = subprocess.run
    jobs = []
    cancelled = []

    def run(args, **kw):
        if args[0] == "sbatch":
            if len(jobs) == fail_at:
                raise subprocess.CalledProcessError(1, args, stderr="allocation rejected")
            jid = str(700 + len(jobs))
            jobs.append(jid)
            return subprocess.CompletedProcess(args, 0, f"Submitted batch job {jid}\n", "")
        if args[0] == "scancel":
            cancelled.extend(args[1:])
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    return jobs, cancelled


def test_one_record_and_replica_logs_across_jobs(bundle, monkeypatch, tmp_path):
    source, _spec, db = bundle
    jobs, _ = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db, strict=True)
    store = StateStore(db)
    record = store.get(result["id"])
    assert len(store.list()) == 1 and record.job_id is None
    assert jobs == ["700", "701", "702", "703"]
    assert store.get("702").id == record.id
    policy = [t for t in store.tasks(record.id) if t.name == "policy"]
    assert [(t.replica_rank, t.job_id) for t in policy] == [(0, "701"), (1, "702")]
    for t in policy:
        path = Path(t.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(t.job_id)
    assert [r["content"] for r in Controller(db).logs(record.id, task="policy")] == ["701", "702"]
    scripts = {a["name"]: Path(a["script_path"]).read_text() for a in record.allocations}
    assert "${jobs.cpu}" not in scripts["policy-0"] and "700" in scripts["policy-0"]
    assert all(jid in scripts["eval"] for jid in jobs[:-1])
    assert resource_summary(record)["nodes"] == 4
    assert "export APPTAINERENV_BEAKER_REPLICA_RANK=1" in scripts["policy-1"]
    assert "export APPTAINERENV_BEAKER_REPLICA_COUNT=2" in scripts["policy-1"]
    assert record.id in scripts["policy-1"]


def test_partial_submission_rolls_back_exact_owned_jobs(bundle, monkeypatch):
    source, _, db = bundle
    jobs, cancelled = fake_submit(monkeypatch, fail_at=2)
    with pytest.raises(RexsError, match="submission failed"):
        Rexs().submit(str(source), db=db)
    store = StateStore(db)
    record = store.list()[0]
    assert len(store.list()) == 1 and record.status == "SUBMISSION_FAILED"
    assert jobs == cancelled == ["700", "701"]
    assert all(a["status"] == "CANCELLED" for a in record.allocations)


def test_cancel_covers_all_jobs_once(bundle, monkeypatch):
    source, _, db = bundle
    jobs, cancelled = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    Controller(db).cancel(result["id"])
    Controller(db).cancel(result["id"])
    assert cancelled == jobs
    assert StateStore(db).get(result["id"]).status == "CANCELLED"


def test_completion_success_cancels_services_without_failing_parent(bundle, monkeypatch):
    source, _, db = bundle
    jobs, cancelled = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    controller = Controller(db)
    monkeypatch.setattr(
        controller,
        "_slurm_status",
        lambda jid: ("COMPLETED", "0:0", "done") if jid == jobs[-1] else ("RUNNING", None, "running"),
    )
    monkeypatch.setattr(controller, "refresh_runtimes", lambda: None)
    monkeypatch.setattr(controller, "refresh_start_estimates", lambda: None)
    controller.refresh(result["id"])
    record = controller.store.get(result["id"])
    assert record.status == "COMPLETED" and cancelled == jobs[:-1]
    assert len(controller.store.list()) == 1


def test_one_preempted_replica_does_not_finish_running_experiment(bundle, monkeypatch):
    source, _, db = bundle
    jobs, cancelled = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    controller = Controller(db)
    monkeypatch.setattr(
        controller,
        "_slurm_status",
        lambda jid: ("PREEMPTED", "0:0", "preempted") if jid == jobs[1] else ("RUNNING", None, "running"),
    )
    monkeypatch.setattr(controller, "refresh_runtimes", lambda: None)
    monkeypatch.setattr(controller, "refresh_start_estimates", lambda: None)
    controller.refresh(result["id"])
    assert controller.store.get(result["id"]).status == "RUNNING" and not cancelled


@pytest.mark.parametrize("change", ["duplicate", "missing", "forward", "completion_replicas"])
def test_invalid_plans_rejected_before_any_submission(bundle, monkeypatch, change):
    source, spec, db = bundle
    if change == "duplicate":
        spec["rexs"]["allocations"][1]["tasks"] = ["redis"]
    if change == "missing":
        spec["rexs"]["allocations"].pop(1)
    if change == "forward":
        spec["tasks"][0]["envVars"] = [{"name": "JOB", "value": "${jobs.eval}"}]
    if change == "completion_replicas":
        spec["tasks"][2]["replicas"] = 2
    source.write_text(yaml.safe_dump(spec))
    jobs, cancelled = fake_submit(monkeypatch)
    with pytest.raises(ConfigurationError):
        Rexs().submit(str(source), db=db)
    assert jobs == cancelled == [] and not Path(db).exists()


def test_render_and_validate_allocate_nothing(bundle, monkeypatch, tmp_path):
    source, _, _db = bundle
    jobs, _ = fake_submit(monkeypatch)
    result = Rexs().validate(str(source), strict=True)
    assert len(result["allocations"]) == 4
    Rexs().render(str(source), output=str(tmp_path / "scripts"))
    assert len(list((tmp_path / "scripts").glob("*.sbatch"))) == 4 and not jobs


def test_dashboard_lists_one_experiment_and_all_allocation_logs(bundle, monkeypatch):
    import base64
    import threading
    from urllib.request import Request, urlopen

    from rexs.server import RexsServer

    source, _, db = bundle
    fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    monkeypatch.setenv("REXS_CAPTURE_WANDB", "0")
    server = RexsServer(("127.0.0.1", 0), Controller(db), poll_interval=60)
    headers = {"Authorization": "Basic " + base64.b64encode(server.expected_credentials).decode()}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(Request(base + "/api/experiments", headers=headers)) as response:
            listing = json.load(response)
        assert len(listing) == 1 and len(listing[0]["allocations"]) == 4
        assert all("spec_text" not in a for a in listing[0]["allocations"])
        with urlopen(Request(base + "/api/experiments/" + result["id"], headers=headers)) as response:
            detail = json.load(response)
        assert {t["job_id"] for t in detail["tasks"]} == {"700", "701", "702", "703"}
        assert detail["resources"]["nodes"] == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failed_rollback_retains_jobs_for_retry(bundle, monkeypatch):
    source, _, db = bundle
    jobs, _ = fake_submit(monkeypatch, fail_at=2)
    mocked_run = subprocess.run

    def fail_cancel(args, **kwargs):
        if args[0] == "scancel":
            raise subprocess.CalledProcessError(1, args, stderr="scheduler unavailable")
        return mocked_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_cancel)
    with pytest.raises(RexsError, match="cleanup error"):
        Rexs().submit(str(source), db=db)
    store = StateStore(db)
    record = store.list()[0]
    assert record.status == "UNKNOWN" and record.id in {r.id for r in store.active()}
    assert [a["job_id"] for a in record.allocations if a["status"] == "SUBMITTED"] == jobs
