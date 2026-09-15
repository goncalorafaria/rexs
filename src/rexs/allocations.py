"""Compile and submit one Beaker experiment as independently scheduled allocations."""

from __future__ import annotations

import copy
import hashlib
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from rexs.alerts import validate_alerts
from rexs.compiler import compile_experiment
from rexs.config import load_profile
from rexs.controller import parse_job_id
from rexs.errors import ConfigurationError, RexsError
from rexs.state import StateStore

_JOB_REF = re.compile(r"\$\{jobs\.([A-Za-z0-9_-]+)\}")


def resolve_jobs(value, jobs):
    if isinstance(value, str):

        def resolve(match):
            if match[1] not in jobs:
                raise ConfigurationError(f"Unknown or forward job reference: {match[0]}")
            return jobs[match[1]]

        return _JOB_REF.sub(resolve, value)
    if isinstance(value, list):
        return [resolve_jobs(item, jobs) for item in value]
    if isinstance(value, dict):
        return {key: resolve_jobs(item, jobs) for key, item in value.items()}
    return value


@dataclass
class Allocation:
    name: str
    spec: dict
    profile: object
    tasks: list[dict]
    completion: bool


def plan(spec, source, *, default_profile=None):
    settings = spec.get("rexs")
    if not isinstance(settings, dict) or set(settings) - {"allocations", "completion_task", "failure_policies", "alerts"}:
        raise ConfigurationError("rexs supports allocations, completion_task, failure_policies and alerts")
    groups = settings.get("allocations")
    if groups is None and set(settings) == {"alerts"}:
        groups = [{"name": "main", "tasks": [task.get("name") for task in spec.get("tasks", [])]}]
    if not isinstance(groups, list) or not groups:
        raise ConfigurationError("rexs.allocations must be a nonempty list")
    tasks = spec.get("tasks", [])
    names = [task.get("name") for task in tasks]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ConfigurationError("Every logical task needs a unique name")
    validate_alerts(settings.get("alerts", {}), names)
    by_name = dict(zip(names, tasks))
    completion = settings.get("completion_task")
    if completion is not None and completion not in by_name:
        raise ConfigurationError("completion_task must name a logical task")
    policies = settings.get("failure_policies", {})
    if not isinstance(policies, dict) or set(policies) - set(names):
        raise ConfigurationError("failure_policies must map task names to policies")
    for task_name, policy in policies.items():
        if not isinstance(policy, dict) or set(policy) - {"action", "max_restarts"}:
            raise ConfigurationError("Failure policy fields: action, max_restarts")
        action = policy.get("action")
        if action not in {"continue", "restart", "fail_experiment"}:
            raise ConfigurationError("Failure policy action must be continue, restart or fail_experiment")
        count = policy.get("max_restarts", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ConfigurationError("max_restarts must be a nonnegative integer")
        if action == "restart" and (count < 1 or task_name == completion):
            raise ConfigurationError("restart requires max_restarts >= 1 and cannot target the completion task")
        if action != "restart" and count:
            raise ConfigurationError("max_restarts only applies to restart")
    assigned, allocation_names, result = set(), set(), []
    base = {key: value for key, value in spec.items() if key not in ("tasks", "rexs")}
    for group in groups:
        if not isinstance(group, dict) or set(group) - {"name", "tasks", "profile", "independent_replicas"}:
            raise ConfigurationError("Allocation fields: name, tasks, profile, independent_replicas")
        name, selected = group.get("name"), group.get("tasks")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise ConfigurationError("Allocation name must be a simple identifier")
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(x, str) or x not in by_name or x in assigned for x in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ConfigurationError("Each task must belong to exactly one allocation group")
        assigned.update(selected)
        profile_path = group.get("profile", default_profile)
        profile = load_profile(Path(source).parent / profile_path if profile_path else None)
        independent = group.get("independent_replicas", False)
        if not isinstance(independent, bool) or independent and len(selected) != 1:
            raise ConfigurationError("independent_replicas requires exactly one task")
        selected_tasks = [copy.deepcopy(by_name[item]) for item in selected]
        for task in selected_tasks:
            replicas = task.get("replicas", 1)
            if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
                raise ConfigurationError("replicas must be a positive integer")
        count = selected_tasks[0].get("replicas", 1) if independent else 1
        if completion in selected and (len(selected) != 1 or count != 1 or selected_tasks[0].get("replicas", 1) != 1):
            raise ConfigurationError("The completion task must have one replica in its own allocation")
        for rank in range(count):
            allocation_name = f"{name}-{rank}" if independent else name
            if allocation_name in allocation_names:
                raise ConfigurationError(f"Duplicate allocation: {allocation_name}")
            allocation_names.add(allocation_name)
            local_tasks = copy.deepcopy(selected_tasks)
            if independent:
                local_tasks[0]["replicas"] = 1
                env = local_tasks[0].setdefault("envVars", [])
                env[:] = [e for e in env if e["name"] not in ("BEAKER_REPLICA_RANK", "BEAKER_REPLICA_COUNT")]
                env.extend(
                    [
                        {"name": "BEAKER_REPLICA_RANK", "value": str(rank)},
                        {"name": "BEAKER_REPLICA_COUNT", "value": str(count)},
                    ]
                )
            bindings = [
                {"name": task["name"], "rank": rank if independent else local_rank, "local_rank": local_rank}
                for task in local_tasks
                for local_rank in range(task.get("replicas", 1))
            ]
            result.append(
                Allocation(allocation_name, {**base, "tasks": local_tasks}, profile, bindings, completion in selected)
            )
    if assigned != set(names):
        raise ConfigurationError(f"Unassigned tasks: {sorted(set(names) - assigned)}")
    for allocation in result:
        assigned_policies = {
            (policies.get(t["name"], {}).get("action", "continue"), policies.get(t["name"], {}).get("max_restarts", 0))
            for t in allocation.tasks
        }
        if len(assigned_policies) != 1:
            raise ConfigurationError("Tasks sharing an allocation must use the same failure policy")
        if next(iter(assigned_policies))[0] == "restart":
            if any("${jobs." + allocation.name + "}" in str(other.spec) for other in result):
                raise ConfigurationError(
                    "Cannot restart an allocation referenced by ${jobs.NAME}; use stable discovery"
                )
            if allocation.profile.cleanup_job_ids or allocation.profile.cleanup_job_file:
                raise ConfigurationError("Cannot restart an allocation with cleanup_job_ids")
    # Completion is submitted last so its trap can own every service job.
    result.sort(key=lambda item: item.completion)
    return result


def compile_plan(
    spec, source, *, default_profile=None, name="experiment", strict=False, image_map=None, dataset_map=None
):
    allocations = plan(spec, source, default_profile=default_profile)
    jobs, compiled = {}, []
    for index, allocation in enumerate(allocations):
        resolved = resolve_jobs(allocation.spec, jobs)
        profile = allocation.profile
        if allocation.completion:
            profile = replace(profile, cleanup_job_ids=tuple(jobs.values()))
        result = compile_experiment(
            resolved,
            profile=profile,
            job_name=f"{name}-{allocation.name}",
            image_map=image_map,
            dataset_map=dataset_map,
        )
        if strict and result.warnings:
            raise ConfigurationError("\n".join(result.warnings))
        subprocess.run(["bash", "-n"], input=result.script, text=True, capture_output=True, check=True)
        compiled.append((allocation, result))
        jobs[allocation.name] = str(900000 + index)  # Validation only; replaced with actual IDs at submission.
    return compiled


def submit(
    spec,
    source,
    *,
    name,
    default_profile=None,
    db=None,
    strict=False,
    sbatch_args=(),
    output=None,
    image_map=None,
    dataset_map=None,
):
    compiled = compile_plan(
        spec,
        source,
        default_profile=default_profile,
        name=name,
        strict=strict,
        image_map=image_map,
        dataset_map=dataset_map,
    )
    if sbatch_args and any(p.get("action") == "restart" for p in spec["rexs"].get("failure_policies", {}).values()):
        raise ConfigurationError("Restartable tasks require sbatch options in profiles, not submission arguments")
    store = StateStore(db)
    spec_text = yaml.safe_dump(spec, sort_keys=False)
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    # The one parent record is the only experiment. Allocations never create child experiments.
    record = store.create_experiment(
        name=name,
        spec_path=str(Path(source).resolve()),
        spec_text=spec_text,
        spec_sha256=digest(spec_text),
        script_path="",
        script_sha256="",
        run_root=compiled[0][0].profile.run_root,
        warnings=[warning for _, result in compiled for warning in result.warnings],
        tasks=spec["tasks"],
    )
    directory = Path(output).expanduser() if output else store.path.parent / "artifacts" / record.id
    directory.mkdir(parents=True, exist_ok=True)
    for ordinal, (allocation, result) in enumerate(compiled):
        target = directory / f"{allocation.name}.sbatch"
        store.add_allocation(
            record.id,
            name=allocation.name,
            ordinal=ordinal,
            spec_text=yaml.safe_dump(allocation.spec),
            script_path=str(target.resolve()),
            script_sha256=digest(result.script),
            run_root=allocation.profile.run_root,
            tasks=allocation.tasks,
            completion=allocation.completion,
        )
    jobs = {}
    try:
        for allocation, _ in compiled:
            profile = allocation.profile
            if allocation.completion:
                profile = replace(
                    profile,
                    cleanup_job_ids=tuple(jobs.values()),
                    cleanup_job_file=str((directory / "owned-jobs.txt").resolve()),
                )
            resolved = resolve_jobs(allocation.spec, jobs)
            for task in resolved["tasks"]:
                env = task.setdefault("envVars", [])
                env[:] = [e for e in env if e["name"] not in ("REXS_EXPERIMENT_ID", "REXS_ALLOCATION_NAME")]
                env.extend(
                    [
                        {"name": "REXS_EXPERIMENT_ID", "value": record.id},
                        {"name": "REXS_ALLOCATION_NAME", "value": allocation.name},
                    ]
                )
            result = compile_experiment(
                resolved,
                profile=profile,
                job_name=f"{name}-{allocation.name}",
                image_map=image_map,
                dataset_map=dataset_map,
            )
            target = directory / f"{allocation.name}.sbatch"
            target.write_text(result.script)
            target.chmod(0o700)
            store.update_allocation(
                record.id,
                allocation.name,
                "GENERATED",
                script_path=str(target.resolve()),
                script_sha256=digest(result.script),
            )
            completed = subprocess.run(
                ["sbatch", *sbatch_args, str(target)], check=True, text=True, capture_output=True
            )
            jobs[allocation.name] = parse_job_id(completed.stdout)
            with (directory / "owned-jobs.txt").open("a") as owned:
                owned.write(jobs[allocation.name] + "\n")
            store.update_allocation(
                record.id, allocation.name, "SUBMITTED", job_id=jobs[allocation.name], detail=completed.stdout.strip()
            )
        store.update_status(record.id, "SUBMITTED", detail=f"Submitted {len(jobs)} allocations")
    except BaseException as error:
        cleanup_error = None
        if jobs:
            try:
                subprocess.run(["scancel", *jobs.values()], check=True, text=True, capture_output=True)
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_error = str(exc)
        for allocation, _ in compiled:
            if allocation.name not in jobs or cleanup_error is None:
                store.update_allocation(record.id, allocation.name, "CANCELLED", detail="Submission rollback")
        # Keep cleanup failures active and visible so cancellation can be retried.
        store.update_status(
            record.id,
            "UNKNOWN" if cleanup_error else "SUBMISSION_FAILED",
            detail=f"{error}; cleanup error: {cleanup_error}",
        )
        raise RexsError(f"Experiment {record.id} submission failed: {error}; cleanup error: {cleanup_error}") from error
    return store.get(record.id).as_dict()
