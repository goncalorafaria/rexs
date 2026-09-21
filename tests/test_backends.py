"""Backend isolation, lossless planning, and lifecycle tests without a cluster."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from rexs.cli import Rexs
from rexs.config import load_profile
from rexs.controller import Controller
from rexs.errors import ConfigurationError, RexsError
from rexs.ir import Experiment
from rexs.profiles import BeakerProfile, load_execution_profile
from rexs.state import StateStore


@pytest.fixture
def portable(tmp_path):
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(
        yaml.safe_dump(
            {
                "version": "rexs/v1",
                "tasks": [
                    {
                        "name": "worker",
                        "image": "trainer",
                        "command": ["echo", "hello"],
                        "replicas": 2,
                        "datasets": [{"mountPath": "/data", "source": {"ref": "training"}, "readOnly": True}],
                        "envVars": [{"name": "TOKEN", "secret": "token"}],
                        "resources": {"gpuCount": 1},
                    }
                ],
            }
        )
    )
    slurm = tmp_path / "slurm.yaml"
    slurm.write_text(
        yaml.safe_dump(
            {
                "backend": "slurm",
                "slurm": {"partition": "gpu"},
                "runtime": {"kind": "apptainer"},
                "images": {"trainer": "/images/trainer.sif"},
                "datasets": {"training": "/shared/training"},
            }
        )
    )
    beaker = tmp_path / "beaker.yaml"
    beaker.write_text(
        yaml.safe_dump(
            {
                "backend": "beaker",
                "beaker": {
                    "workspace": "org/research",
                    "budget": "org/budget",
                    "priority": "normal",
                    "clusters": ["org/gpu"],
                },
                "images": {"trainer": "org/trainer"},
                "datasets": {"training": "dataset-id"},
            }
        )
    )
    return experiment, slurm, beaker


def test_ir_is_lossless_and_planning_cannot_mutate_source():
    original = {
        "version": "v2",
        "workspace": "org/ws",
        "retry": {"allowedTaskRetries": 3},
        "tasks": [{"name": "x", "image": {"docker": "ubuntu"}, "context": {"priority": "high"}}],
    }
    ir = Experiment.from_dict(original)
    planned = ir.for_slurm()
    planned["tasks"][0]["image"]["docker"] = "changed"
    assert ir.to_beaker() == original
    assert Experiment.from_dict(ir.to_dict()).to_beaker() == original


def test_legacy_and_nested_slurm_profiles_match(tmp_path):
    old = tmp_path / "old.yaml"
    new = tmp_path / "new.yaml"
    old.write_text("partition: gpu\nimages: {trainer: /image.sif}\n")
    new.write_text("backend: slurm\nslurm: {partition: gpu}\nimages: {trainer: /image.sif}\n")
    assert load_profile(old) == load_profile(new)


@pytest.mark.parametrize(
    "value",
    [
        {"backend": "oops"},
        {"backend": "beaker", "partition": "gpu"},
        {"backend": "beaker", "beaker": {"clusters": "org/gpu"}},
        {"backend": "slurm", "runtime": {"kind": "docker"}},
    ],
)
def test_invalid_backend_profiles_fail(tmp_path, value):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(ConfigurationError):
        load_execution_profile(path)


def test_native_ir_slurm_render(portable):
    experiment, profile, _ = portable
    script = Rexs().render(str(experiment), profile=str(profile), strict=True)
    assert "#SBATCH --partition=gpu" in script
    assert "/images/trainer.sif" in script
    assert "/shared/training:/data:ro" in script
    assert Rexs().normalize(str(experiment))["version"] == "rexs/v1"


@pytest.fixture
def beaker_sdk():
    return pytest.importorskip("beaker", reason="optional Beaker SDK")


def test_same_experiment_beaker_render_and_dry_run(portable, beaker_sdk, monkeypatch):
    experiment, _, profile = portable
    monkeypatch.setattr(beaker_sdk.Beaker, "from_env", lambda **kw: pytest.fail("offline planning contacted Beaker"))
    spec = yaml.safe_load(Rexs().render(str(experiment), profile=str(profile), strict=True))
    task = spec["tasks"][0]
    assert spec["version"] == "v2" and spec["budget"] == "org/budget"
    assert task["image"] == {"beaker": "org/trainer"}
    assert task["datasets"][0]["source"] == {"beaker": "dataset-id"}
    assert task["datasets"][0]["mode"] == "readonly"
    assert task["envVars"] == [{"name": "TOKEN", "secret": "token"}]
    assert task["constraints"]["cluster"] == ["org/gpu"]
    assert Rexs().validate(str(experiment), profile=str(profile))["backend"] == "beaker"
    assert Rexs().dry_run(str(experiment), profile=str(profile), strict=True)["passed"] == 1


@pytest.mark.parametrize(
    "edit",
    [
        {"rexs": {"completion_task": "worker"}},
        {"mystery": True},
    ],
)
def test_beaker_rejects_unsupported_semantics(portable, beaker_sdk, edit):
    from rexs.beaker_backend import plan

    experiment, _, profile = portable
    spec = yaml.safe_load(experiment.read_text())
    spec.update(edit)
    with pytest.raises(ConfigurationError):
        plan(spec, load_execution_profile(profile), name="test")


def test_beaker_preserves_native_fields_and_supports_image_defaults(beaker_sdk):
    from rexs.beaker_backend import plan

    spec = {
        "version": "v2",
        "retry": {"allowedTaskRetries": 2},
        "tasks": [
            {
                "name": "x",
                "image": {"docker": "ubuntu"},
                "propagateFailure": False,
                "context": {"priority": "high", "preemptible": True},
                "datasets": [{"mountPath": "/secret", "source": {"secret": "my-secret"}}],
            }
        ],
    }
    old = deepcopy(spec)
    result = plan(spec, BeakerProfile(), name="native")
    assert spec == old
    assert result.spec == old


@pytest.fixture
def fake_beaker(beaker_sdk, monkeypatch):
    api = beaker_sdk
    workload = api.BeakerWorkload(status=api.BeakerWorkloadStatus.running)
    workload.experiment.id = "beaker-experiment"
    for rank in range(2):
        task = workload.experiment.tasks.add(name="worker", id=f"task-{rank}")
        task.system_details.replica_group_details.rank = rank
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return workload

        def cancel(self, remote):
            calls.append(("cancel", remote.experiment.id))
            return [remote.experiment.id]

    client = Client()
    client.experiment = SimpleNamespace(create=client.create)
    client.workload = SimpleNamespace(
        get=lambda remote_id: workload,
        cancel=client.cancel,
        get_latest_job=lambda w, task: api.BeakerJob(id=f"job-{task.id}"),
    )
    client.job = SimpleNamespace(logs=lambda job, **kwargs: [SimpleNamespace(message=f"{job.id}\n".encode())])
    monkeypatch.setattr(api.Beaker, "from_env", lambda **kwargs: client)
    return calls, workload


def test_beaker_submit_status_logs_cancel_never_use_slurm(portable, fake_beaker, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Beaker invoked a shell command"))
    experiment, _, profile = portable
    db = str(tmp_path / "state.sqlite3")
    result = Rexs().submit(str(experiment), profile=str(profile), db=db)
    assert result["backend"] == "beaker" and result["job_id"] == "beaker-experiment"
    assert Rexs().status(result["id"], db=db)["status"] == "RUNNING"
    logs = Rexs().logs(result["id"], task="worker", replica=1, db=db)
    assert len(logs) == 1 and logs[0]["content"] == "job-task-1\n"
    assert all(t.log_path is None for t in StateStore(db).tasks(result["id"]))
    assert Rexs().cancel(result["id"], db=db)["status"] == "CANCELLED"
    assert fake_beaker[0][-1] == ("cancel", "beaker-experiment")
    with pytest.raises(ConfigurationError, match="only on Slurm"):
        Rexs().extend(result["id"], "worker", db=db)
    with pytest.raises(RexsError, match="remotely"):
        Rexs().log_paths(result["id"], db=db)


def test_mixed_backend_refresh_isolates_scheduler_ids(portable, fake_beaker, tmp_path):
    import subprocess

    experiment, _, profile = portable
    db = str(tmp_path / "state.sqlite3")
    Rexs().submit(str(experiment), profile=str(profile), db=db)
    store = StateStore(db)
    slurm = store.create_experiment(
        name="slurm",
        spec_path="",
        spec_text="",
        spec_sha256="",
        script_path="",
        script_sha256="",
        run_root="",
        warnings=(),
        tasks=[],
    )
    store.record_submission(slurm.id, "123", "submitted")
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        assert "beaker-experiment" not in " ".join(args)
        return subprocess.CompletedProcess(args, 0, stdout="RUNNING\n" if args[-1] == "--format=%T" else "", stderr="")

    updates = Controller(db, runner=run).refresh()
    assert len(updates) == 2 and commands


def test_beaker_submission_failure_keeps_uncertain_record(portable, fake_beaker, beaker_sdk, tmp_path, monkeypatch):
    experiment, _, profile = portable
    client = beaker_sdk.Beaker.from_env()
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("simulated timeout after acceptance")

    monkeypatch.setattr(client.experiment, "create", fail)
    db = str(tmp_path / "failed.sqlite3")
    with pytest.raises(RexsError, match="uncertain"):
        Rexs().submit(str(experiment), profile=str(profile), db=db)
    records = StateStore(db).list()
    assert len(calls) == len(records) == 1
    assert records[0].status == "UNKNOWN" and records[0].job_id is None
    assert records[0].backend == "beaker"
    assert yaml.safe_load(records[0].spec_text)["workspace"] == "org/research"


def test_beaker_unknown_nested_field_is_not_dropped(beaker_sdk):
    from rexs.beaker_backend import plan

    spec = {"version": "v2", "tasks": [{"name": "x", "image": {"docker": "ubuntu"}, "resources": {"gpuCout": 1}}]}
    with pytest.raises(ConfigurationError, match="resources.gpuCout"):
        plan(spec, BeakerProfile(), name="typo")


def test_missing_profile_workspace_preserves_sdk_default(fake_beaker, beaker_sdk, monkeypatch):
    from rexs.beaker_backend import _client

    client = beaker_sdk.Beaker.from_env()
    options = []
    monkeypatch.setattr(beaker_sdk.Beaker, "from_env", lambda **kwargs: options.append(kwargs) or client)
    with _client():
        pass
    assert "default_workspace" not in options[0]
