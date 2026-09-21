"""Beaker execution adapter; the SDK is imported only for Beaker operations."""

import hashlib
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import yaml

from rexs.errors import ConfigurationError, RexsError
from rexs.ir import Experiment


def sdk():
    try:
        import beaker
    except ImportError as exc:
        raise ConfigurationError("Beaker execution requires pip install 'rexs[beaker]'") from exc
    if not hasattr(beaker, "BeakerExperimentSpec"):
        raise ConfigurationError("Beaker execution requires beaker-py>=2.8,<3")
    return beaker


@contextmanager
def _client(workspace=None):
    options = {"default_workspace": workspace} if workspace is not None else {}
    try:
        with sdk().Beaker.from_env(check_for_upgrades=False, **options) as client:
            yield client
    except RexsError:
        raise
    except Exception as exc:
        raise RexsError(f"Beaker request failed: {exc}") from exc


@dataclass(frozen=True)
class BeakerPlan:
    spec: dict
    workspace: str | None
    name: str

    def render(self):
        return yaml.safe_dump(self.spec, sort_keys=False)


def _resource(value, kinds, label):
    if not isinstance(value, dict) or len(value) != 1 or not set(value) <= kinds:
        raise ConfigurationError(f"{label} must select one of {sorted(kinds)}")
    if not isinstance(next(iter(value.values())), str) or not next(iter(value.values())):
        raise ConfigurationError(f"{label} reference must be a non-empty string")
    return deepcopy(value)


def _reject_dropped_fields(original, normalized, path="experiment"):
    """The SDK ignores unknown keys; fail instead of silently changing intent."""
    if isinstance(original, dict) and isinstance(normalized, dict):
        for key, value in original.items():
            if key not in normalized and value is not None:
                raise ConfigurationError(f"Unsupported Beaker field: {path}.{key}")
            _reject_dropped_fields(value, normalized.get(key), f"{path}.{key}")
    elif isinstance(original, list) and isinstance(normalized, list):
        for index, (value, other) in enumerate(zip(original, normalized)):
            _reject_dropped_fields(value, other, f"{path}[{index}]")


def plan(spec, profile, *, name, image_map=None, dataset_map=None):
    result = Experiment.from_dict(spec).to_beaker()
    if result.get("rexs"):
        raise ConfigurationError(
            "Beaker does not yet support rexs allocation/completion/failure policies; use native Beaker lifecycle fields"
        )
    result.pop("rexs", None)
    settings = profile.settings
    workspace = settings.get("workspace", result.pop("workspace", None))
    result.pop("workspace", None)
    result.pop("name", None)
    if "budget" in settings:
        result["budget"] = settings["budget"]
    images = {**profile.images, **(image_map or {})}
    datasets = {**profile.datasets, **(dataset_map or {})}
    for index, task in enumerate(result["tasks"]):
        task.setdefault("name", f"task-{index}")
        original = task.get("image")
        if isinstance(original, str):
            if original not in images:
                raise ConfigurationError(f"Unmapped logical image: {original}")
            mapped = images[original]
        else:
            original = _resource(original, {"beaker", "docker"}, "image")
            kind, ref = next(iter(original.items()))
            mapped = images.get(ref, images.get(f"{kind}:{ref}", original))
        if isinstance(mapped, str):
            if mapped.startswith("/") or mapped.endswith(".sif"):
                raise ConfigurationError("Beaker image mappings cannot point to local SIF files")
            mapped = {"docker": mapped[9:]} if mapped.startswith("docker://") else {"beaker": mapped}
        task["image"] = _resource(mapped, {"beaker", "docker"}, "image mapping")
        for mount in task.get("datasets", []):
            kinds = {"beaker", "weka", "hostPath", "result", "secret", "volume"}
            original = _resource(mount.get("source"), kinds | {"ref"}, "dataset source")
            kind, ref = next(iter(original.items()))
            mapped = datasets.get(ref, datasets.get(f"{kind}:{ref}", original))
            if kind == "ref" and mapped == original:
                raise ConfigurationError(f"Unmapped logical dataset: {ref}")
            mount["source"] = _resource(
                {"beaker": mapped} if isinstance(mapped, str) else mapped, kinds, "dataset mapping"
            )
            if "readOnly" in mount:
                readonly = mount.pop("readOnly")
                if not isinstance(readonly, bool):
                    raise ConfigurationError("dataset readOnly must be a boolean")
                mode = "readonly" if readonly else "readwrite"
                if "mode" in mount and mount["mode"] != mode:
                    raise ConfigurationError("Conflicting dataset mode and readOnly")
                mount["mode"] = mode
        context = {**settings.get("context", {}), **task.get("context", {})}
        if "priority" in settings:
            context["priority"] = settings["priority"]
        task["context"] = context
        if "clusters" in settings:
            task.setdefault("constraints", {})["cluster"] = deepcopy(settings["clusters"])
        replicas = task.get("replicas", 1)
        if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
            raise ConfigurationError("replicas must be a positive integer")
    try:
        normalized = sdk().BeakerExperimentSpec.from_json(result).to_json()
        _reject_dropped_fields(result, normalized)
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigurationError(f"Invalid Beaker experiment: {exc}") from exc
    return BeakerPlan(result, workspace, name)


def submit(planned, source, *, db=None, output=None):
    from rexs.state import StateStore

    store = StateStore(db)
    text = planned.render()
    digest = hashlib.sha256(text.encode()).hexdigest()
    snapshot = {**planned.spec, "name": planned.name}
    if planned.workspace is not None:
        snapshot["workspace"] = planned.workspace
    snapshot_text = yaml.safe_dump(snapshot, sort_keys=False)
    record = store.create_experiment(
        name=planned.name,
        spec_path=str(Path(source).resolve()),
        spec_text=snapshot_text,
        spec_sha256=hashlib.sha256(snapshot_text.encode()).hexdigest(),
        script_path="",
        script_sha256=digest,
        run_root="",
        warnings=(),
        tasks=planned.spec["tasks"],
        backend="beaker",
    )
    target = Path(output).expanduser() if output else store.path.parent / "artifacts" / record.id / "beaker.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    target.chmod(0o600)
    with store.connect() as connection:
        connection.execute("UPDATE experiments SET script_path=? WHERE id=?", (str(target.resolve()), record.id))
    try:
        with _client(planned.workspace) as client:
            workload = client.experiment.create(
                spec=sdk().BeakerExperimentSpec.from_json(planned.spec), name=planned.name
            )
            remote_id = workload.experiment.id
            if not remote_id:
                raise RexsError("Beaker returned no experiment ID; inspect Beaker before retrying")
            submitted = store.record_submission(record.id, remote_id, f"Beaker experiment {remote_id}")
    except Exception as exc:
        store.update_status(
            record.id, "UNKNOWN", detail="Beaker submission uncertain; inspect remote state before retrying"
        )
        raise RexsError(f"Beaker submission uncertain for REXS {record.id}: {exc}") from exc
    return submitted.as_dict()


def status(remote_id):
    api = sdk()
    with _client() as client:
        workload = client.workload.get(remote_id)
        state = api.BeakerWorkloadStatus(workload.status).name
    return {
        "submitted": "SUBMITTED",
        "queued": "PENDING",
        "ready_to_start": "PENDING",
        "initializing": "PENDING",
        "running": "RUNNING",
        "stopping": "RUNNING",
        "uploading_results": "RUNNING",
        "canceled": "CANCELLED",
        "succeeded": "COMPLETED",
        "failed": "FAILED",
    }.get(state, "UNKNOWN")


def cancel(remote_id):
    with _client() as client:
        cancelled = list(client.workload.cancel(client.workload.get(remote_id)))
        if not cancelled:
            raise RexsError("Beaker did not confirm cancellation; refresh the experiment status")


def logs(record, *, task=None, replica=None, lines=200):
    output = []
    with _client() as client:
        workload = client.workload.get(record.job_id)
        for remote_task in workload.experiment.tasks:
            rank = remote_task.system_details.replica_group_details.rank
            if (task is not None and remote_task.name != task) or (replica is not None and rank != replica):
                continue
            job = client.workload.get_latest_job(workload, task=remote_task)
            content = (
                ""
                if job is None
                else "".join(
                    event.message.decode("utf-8", errors="replace")
                    for event in client.job.logs(job, tail_lines=lines, follow=False)
                )
            )
            output.append(
                {
                    "experiment_id": record.id,
                    "name": remote_task.name,
                    "replica_rank": rank,
                    "job_id": job.id if job else None,
                    "log_path": None,
                    "result_path": None,
                    "allocation": None,
                    "exists": job is not None,
                    "content": content,
                }
            )
    return output
