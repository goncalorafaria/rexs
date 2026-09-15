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


@pytest.mark.parametrize("action", ["restart", "fail_experiment"])
def test_failure_policy_applies_to_each_independent_replica(bundle, monkeypatch, action):
    source, spec, db = bundle
    spec["rexs"]["failure_policies"] = {
        "policy": {"action": action, **({"max_restarts": 1} if action == "restart" else {})}
    }
    source.write_text(yaml.safe_dump(spec))
    jobs, cancelled = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    controller = Controller(db)
    states = {"701": "PREEMPTED"}
    monkeypatch.setattr(controller, "_slurm_status", lambda jid: (states.get(jid, "RUNNING"), None, "test"))
    monkeypatch.setattr(controller, "refresh_runtimes", lambda: None)
    monkeypatch.setattr(controller, "refresh_start_estimates", lambda: None)
    controller.refresh(result["id"])
    record = controller.store.get(result["id"])
    if action == "fail_experiment":
        assert record.status == "FAILED"
        assert cancelled == ["700", "702", "703"]
        controller.refresh(record.id)
        assert len(jobs) == 4
        return
    assert len(jobs) == 5 and not cancelled
    replica = next(a for a in record.allocations if a["name"] == "policy-0")
    assert replica["job_id"] == "704" and replica["exit_code"] is None
    assert replica["attempts"][0]["job_id"] == "701"
    assert replica["attempts"][0]["status"] == "PREEMPTED"
    assert next(a for a in record.allocations if a["name"] == "policy-1")["job_id"] == "702"
    assert "704" in (Path(replica["script_path"]).parent / "owned-jobs.txt").read_text()
    controller.refresh(record.id)
    assert len(jobs) == 5  # No duplicate replacement on the next controller poll.
    states["704"] = "OUT_OF_MEMORY"
    controller.refresh(record.id)
    assert controller.store.get(record.id).status == "FAILED"
    assert cancelled == ["700", "702", "703"]
    assert any("budget exhausted" in (e["detail"] or "") for e in controller.store.events(record.id))


@pytest.mark.parametrize("finish", ["completion", "cancel"])
def test_terminal_experiment_never_restarts_workers(bundle, monkeypatch, finish):
    source, spec, db = bundle
    spec["rexs"]["failure_policies"] = {"policy": {"action": "restart", "max_restarts": 2}}
    source.write_text(yaml.safe_dump(spec))
    jobs, _ = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    controller = Controller(db)
    monkeypatch.setattr(
        controller, "_slurm_status", lambda jid: ("COMPLETED" if jid == "703" else "PREEMPTED", None, "test")
    )
    monkeypatch.setattr(controller, "refresh_runtimes", lambda: None)
    monkeypatch.setattr(controller, "refresh_start_estimates", lambda: None)
    if finish == "cancel":
        controller.cancel(result["id"])
    controller.refresh(result["id"])
    controller.refresh(result["id"])
    assert len(jobs) == 4
    assert controller.store.get(result["id"]).status == ("COMPLETED" if finish == "completion" else "CANCELLED")


@pytest.mark.parametrize(
    "task,policy",
    [
        ("missing", {"action": "continue"}),
        ("policy", {"action": "restart"}),
        ("policy", {"action": "restart", "max_restarts": True}),
        ("policy", {"action": "typo"}),
        ("evaluation", {"action": "restart", "max_restarts": 2}),
        ("redis", {"action": "restart", "max_restarts": 2}),
    ],
)
def test_invalid_failure_policy_never_submits(bundle, monkeypatch, task, policy):
    source, spec, db = bundle
    spec["rexs"]["failure_policies"] = {task: policy}
    source.write_text(yaml.safe_dump(spec))
    jobs, _ = fake_submit(monkeypatch)
    with pytest.raises(ConfigurationError):
        Rexs().submit(str(source), db=db)
    assert not jobs


def test_cancel_serializes_with_inflight_replacement(bundle, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    source, spec, db = bundle
    spec["rexs"]["failure_policies"] = {"policy": {"action": "restart", "max_restarts": 2}}
    source.write_text(yaml.safe_dump(spec))
    jobs, cancelled = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    entered, release = Event(), Event()
    original_run = subprocess.run

    def blocked_submit(args, **kwargs):
        if args[0] == "sbatch":
            entered.set()
            assert release.wait(5)
        return original_run(args, **kwargs)

    controller = Controller(db, runner=blocked_submit)
    monkeypatch.setattr(
        controller, "_slurm_status", lambda jid: ("PREEMPTED" if jid == "701" else "RUNNING", None, "test")
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(controller._refresh_allocations, controller.store.get(result["id"]))
        assert entered.wait(5)
        cancel = pool.submit(Controller(db).cancel, result["id"])
        release.set()
        refresh.result(timeout=5)
        cancel.result(timeout=5)
    assert len(jobs) == 5
    assert "704" in cancelled
    assert controller.store.get(result["id"]).status == "CANCELLED"
    controller._refresh_allocations(controller.store.get(result["id"]))
    assert len(jobs) == 5


def test_uncertain_restart_does_not_duplicate_or_claim_cancellation(bundle, monkeypatch):
    source, spec, db = bundle
    spec["rexs"]["failure_policies"] = {"policy": {"action": "restart", "max_restarts": 2}}
    source.write_text(yaml.safe_dump(spec))
    jobs, cancelled = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), db=db)
    controller = Controller(db)
    controller.store.update_allocation(result["id"], "policy-0", "RESTARTING")
    monkeypatch.setattr(controller, "_slurm_status", lambda jid: ("RUNNING", None, "test"))
    with pytest.raises(ValueError, match="interrupted restart"):
        controller._refresh_allocations(controller.store.get(result["id"]))
    with pytest.raises(ValueError, match="reconciliation"):
        controller.cancel(result["id"])
    assert len(jobs) == 4
    assert cancelled == ["700", "702", "703"]
    assert controller.store.get(result["id"]).status != "CANCELLED"


def test_alert_only_spec_uses_one_allocation(bundle, monkeypatch):
    source, spec, db = bundle
    spec['rexs'] = {'alerts': {'tasks': {'evaluation': {'completed': True}}}}
    spec['tasks'][1]['envVars'] = []
    source.write_text(yaml.safe_dump(spec))
    jobs, _ = fake_submit(monkeypatch)
    result = Rexs().submit(str(source), profile='profile.yaml', db=db)
    assert len(jobs) == 1
    store = StateStore(db)
    store.update_allocation(result['id'], 'main', 'COMPLETED')
    alert, = store.alerts()
    assert alert['task_name'] == 'evaluation'


def test_extend_keeps_parent_and_unique_ranks(bundle, monkeypatch):
    source, _, db = bundle
    _jobs, cancelled = fake_submit(monkeypatch)
    parent = Rexs().submit(str(source), db=db, strict=True)
    monkeypatch.setattr(Controller, '_slurm_status', lambda self, job: ('RUNNING', None, 'running'))
    result = Rexs().extend(parent['id'], task='policy', replicas=2, db=db, strict=True)
    store = StateStore(db)
    assert result['id'] == parent['id'] and len(store.list()) == 1
    assert len(result['allocations']) == 6
    assert [(t.replica_rank, t.job_id) for t in store.tasks(parent['id']) if t.name == 'policy'] == [(0,'701'),(1,'702'),(2,'704'),(3,'705')]
    assert store.get('705').id == parent['id']
    for a in result['allocations'][-2:]:
        script = Path(a['script_path']).read_text()
        assert parent['id'] in script and '700' in script
    completion = next(a for a in result['allocations'] if a['completion'])
    owned = Path(completion['script_path']).parent/'owned-jobs.txt'
    assert {'704','705'} <= set(owned.read_text().splitlines())
    Controller(db).cancel(parent['id'])
    assert {'700','701','702','703','704','705'} <= set(cancelled)


def test_extension_failure_only_cancels_new_jobs(bundle, monkeypatch):
    source, _, db = bundle
    _jobs, cancelled = fake_submit(monkeypatch, fail_at=5)
    parent = Rexs().submit(str(source), db=db, strict=True)
    with pytest.raises(RexsError, match='original jobs preserved'):
        Rexs().extend(parent['id'], task='policy', replicas=2, db=db)
    assert cancelled == ['704']
    record = StateStore(db).get(parent['id'])
    assert all(a['status']=='SUBMITTED' for a in record.allocations[:4])
    assert len(record.allocations)==4  # Rolled-back replicas cannot trigger parent restart policies.


def test_extend_rejects_completion_and_closed_parent(bundle, monkeypatch):
    source, _, db = bundle
    jobs, _ = fake_submit(monkeypatch)
    parent = Rexs().submit(str(source), db=db)
    with pytest.raises(ConfigurationError):Rexs().extend(parent['id'], task='evaluation', db=db)
    StateStore(db).update_status(parent['id'],'COMPLETED')
    with pytest.raises(ConfigurationError):Rexs().extend(parent['id'], task='policy', db=db)
    assert len(jobs)==4


def test_extend_cleans_jobs_when_evaluator_exits_during_submission(bundle, monkeypatch):
    source, _, db = bundle
    _jobs, cancelled = fake_submit(monkeypatch)
    parent = Rexs().submit(str(source), db=db)
    monkeypatch.setattr(Controller,'_slurm_status',lambda self,job:('COMPLETED','0:0','done'))
    with pytest.raises(RexsError, match='Completion task exited'):
        Rexs().extend(parent['id'],task='policy',db=db)
    assert cancelled==['704']
