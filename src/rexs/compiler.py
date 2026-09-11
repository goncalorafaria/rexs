from __future__ import annotations

import math
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rexs.config import SlurmProfile
from rexs.errors import TranslationError

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_TOP_LEVEL_FIELDS = {
    "version",
    "name",
    "description",
    "workspace",
    "budget",
    "tasks",
    "retry",
    "groups",
}
_TASK_FIELDS = {
    "name",
    "image",
    "command",
    "arguments",
    "envVars",
    "datasets",
    "result",
    "resources",
    "context",
    "constraints",
    "replicas",
    "leaderSelection",
    "synchronizedStartTimeout",
    "hostNetworking",
    "propagateFailure",
    "propagatePreemption",
}


@dataclass(frozen=True)
class EnvironmentValue:
    name: str
    value: str | None = None
    secret: str | None = None


@dataclass(frozen=True)
class Bind:
    source: str
    target: str
    read_only: bool = False


@dataclass(frozen=True)
class TaskPlan:
    name: str
    image: str
    command: tuple[str, ...]
    replicas: int
    gpu_count: int
    cpu_count: int
    memory: str | None
    env: tuple[EnvironmentValue, ...]
    binds: tuple[Bind, ...]
    result_path: str | None
    leader_selection: bool


@dataclass(frozen=True)
class CompileResult:
    script: str
    warnings: tuple[str, ...]
    job_name: str
    nodes: int
    tasks: int
    gpus_per_node: int


def compile_experiment(
    spec: Mapping[str, Any],
    *,
    profile: SlurmProfile | None = None,
    job_name: str = "beaker-experiment",
    image_map: Mapping[str, str] | None = None,
    dataset_map: Mapping[str, str] | None = None,
) -> CompileResult:
    """Compile the supported datadev/LiteRegistry Beaker v2 subset to one sbatch script."""

    profile = profile or SlurmProfile()
    warnings: list[str] = []
    if spec.get("version") != "v2":
        raise TranslationError("only Beaker experiment version 'v2' is supported")
    _warn_unknown(spec, _TOP_LEVEL_FIELDS, "experiment", warnings)
    for field in ("workspace", "budget"):
        if field in spec:
            warnings.append(
                f"ignored unsupported experiment.{field}; configure Slurm scheduling through the REXS site profile"
            )
    raw_tasks = spec.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise TranslationError("experiment.tasks must be a non-empty list")
    if "retry" in spec:
        warnings.append("ignored experiment.retry; configure Slurm requeue policy through the site profile")
    if "groups" in spec:
        warnings.append("ignored experiment.groups; REXS does not reproduce Beaker experiment groups")

    images = {**profile.images, **(image_map or {})}
    datasets = {**profile.datasets, **(dataset_map or {})}
    plans = tuple(
        _parse_task(raw, index=index, profile=profile, image_map=images, dataset_map=datasets, warnings=warnings)
        for index, raw in enumerate(raw_tasks)
    )
    task_names = [task.name for task in plans]
    if len(set(task_names)) != len(task_names):
        raise TranslationError("task names must be unique within an experiment")
    replicas = [task for task in plans for _ in range(task.replicas)]
    task_count = len(replicas)
    nodes = math.ceil(task_count / profile.tasks_per_node)
    gpus = max(task.gpu_count for task in plans)
    if gpus and not profile.completion_task and any(task.gpu_count < gpus for task in plans):
        warnings.append(
            f"homogeneous allocation requests {gpus} GPU(s) on every node; CPU-only or smaller tasks leave some idle"
        )
    cpus = max(task.cpu_count for task in plans)
    memory = _largest_memory(plans, warnings) or profile.memory
    if profile.tasks_per_node > 1:
        if gpus and not profile.completion_task:
            raise TranslationError("GPU task packing requires an explicit completion_task lifecycle")
        if any(task.memory is None for task in plans):
            raise TranslationError("packed tasks require explicit resources.memory for concurrent Slurm steps")
        try:
            packed_memory = max(
                sum(_memory_mib(task.memory) for task in replicas[start : start + profile.tasks_per_node])
                for start in range(0, task_count, profile.tasks_per_node)
            )
            memory = _slurm_memory(str(max(packed_memory, _memory_mib(profile.memory or "0M"))))
        except ValueError as exc:
            raise TranslationError("packed tasks require valid task and profile memory sizes") from exc
    if profile.completion_task:
        matching = [task for task in plans if task.name == profile.completion_task]
        if nodes != 1 or len(matching) != 1 or matching[0].replicas != 1:
            raise TranslationError("completion_task requires one matching replica and a single packed node")
        if any(task.memory is None for task in plans):
            raise TranslationError("completion_task requires explicit memory for each task")
        cpus = sum(task.cpu_count for task in replicas)
        gpus = sum(task.gpu_count for task in replicas)
    if profile.shared_cpus_per_node is not None:
        pool = profile.shared_cpus_per_node
        if isinstance(pool, bool) or not isinstance(pool, int) or pool < 1:
            raise TranslationError("shared_cpus_per_node must be a positive integer")
        if not profile.completion_task or nodes != 1:
            raise TranslationError("shared_cpus_per_node requires a single-node completion_task allocation")
        if any(task.cpu_count > pool for task in plans):
            raise TranslationError("task CPU request exceeds shared_cpus_per_node")
        cpus = pool
    safe_job_name = _safe(job_name)[:128] or "beaker-experiment"
    lines = _headers(
        safe_job_name,
        nodes=nodes,
        tasks=1 if profile.completion_task else task_count,
        gpus=gpus,
        cpus=cpus,
        memory=memory,
        partition=profile.partition,
        account=profile.account,
        qos=profile.qos,
        profile=profile,
    )
    lines.extend(
        [
            "",
            "set -Eeuo pipefail",
            "umask 077",
            "",
            "# Generated by REXS from a Beaker v2 experiment.",
        ]
    )
    lines.extend(f"# WARNING: {warning}" for warning in warnings)
    lines.extend(_runtime_preamble(profile, nodes))
    lines.extend(_image_preamble(plans, profile))
    lines.extend(_task_launches(plans, profile))
    lines.extend(_supervisor(profile.completion_task))
    return CompileResult(
        script="\n".join(lines) + "\n",
        warnings=tuple(warnings),
        job_name=safe_job_name,
        nodes=nodes,
        tasks=task_count,
        gpus_per_node=gpus,
    )


def _parse_task(
    raw: Any,
    *,
    index: int,
    profile: SlurmProfile,
    image_map: Mapping[str, str],
    dataset_map: Mapping[str, str],
    warnings: list[str],
) -> TaskPlan:
    if not isinstance(raw, dict):
        raise TranslationError(f"tasks[{index}] must be a mapping")
    name = raw.get("name", f"task-{index}")
    if not isinstance(name, str) or not name:
        raise TranslationError(f"tasks[{index}].name must be a non-empty string")
    location = f"task {name!r}"
    _warn_unknown(raw, _TASK_FIELDS, location, warnings)
    for field in ("context", "constraints"):
        if field in raw:
            warnings.append(
                f"ignored unsupported {location}.{field}; configure Slurm scheduling through the REXS site profile"
            )
    if "synchronizedStartTimeout" in raw:
        warnings.append(
            f"ignored unsupported {location}.synchronizedStartTimeout; REXS does not enforce Beaker start deadlines"
        )
    if "propagatePreemption" in raw:
        warnings.append(
            f"ignored unsupported {location}.propagatePreemption; Slurm preemption applies to the entire allocation"
        )

    replicas = _positive_int(raw.get("replicas", 1), f"{location}.replicas")
    command = raw.get("command", [])
    arguments = raw.get("arguments", [])
    if not isinstance(command, list) or not all(_scalar(item) for item in command):
        raise TranslationError(f"{location}.command must be a list of scalar arguments")
    if not isinstance(arguments, list) or not all(_scalar(item) for item in arguments):
        raise TranslationError(f"{location}.arguments must be a list of scalar arguments")
    command_tuple = tuple(str(item) for item in (*command, *arguments))
    if not command_tuple:
        raise TranslationError(f"{location} needs command or arguments; image-default commands are not supported")

    image = _resolve_image(raw.get("image"), location, profile, image_map, warnings)
    resources = raw.get("resources") or {}
    if not isinstance(resources, dict):
        raise TranslationError(f"{location}.resources must be a mapping")
    gpu_count = _nonnegative_int(resources.get("gpuCount", 0), f"{location}.resources.gpuCount")
    cpu_count = _positive_int(resources.get("cpuCount", profile.cpus_per_task), f"{location}.resources.cpuCount")
    memory = resources.get("memory")
    if memory is not None and not isinstance(memory, (str, int, float)):
        raise TranslationError(f"{location}.resources.memory must be a scalar")
    for ignored in sorted(set(resources) - {"gpuCount", "cpuCount", "memory", "sharedMemory"}):
        warnings.append(f"ignored unsupported {location}.resources.{ignored}")
    if resources.get("sharedMemory") is not None:
        warnings.append(f"ignored {location}.resources.sharedMemory; Apptainer uses the host IPC namespace by default")

    env = _parse_env(raw.get("envVars", ()), location, warnings)
    binds = _parse_datasets(raw.get("datasets", ()), location, profile, dataset_map, warnings)
    result = raw.get("result") or {}
    if not isinstance(result, dict):
        raise TranslationError(f"{location}.result must be a mapping")
    result_path = result.get("path")
    if result_path is not None and (not isinstance(result_path, str) or not result_path.startswith("/")):
        raise TranslationError(f"{location}.result.path must be an absolute path")
    if set(result) - {"path"}:
        warnings.append(f"ignored unsupported fields in {location}.result: {', '.join(sorted(set(result) - {'path'}))}")

    if raw.get("hostNetworking") is False:
        warnings.append(f"ignored {location}.hostNetworking=false; Apptainer shares the host network")
    if raw.get("propagateFailure") is False:
        warnings.append(f"{location}.propagateFailure=false is ignored; replica failures terminate the allocation")
    return TaskPlan(
        name=name,
        image=image,
        command=command_tuple,
        replicas=replicas,
        gpu_count=gpu_count,
        cpu_count=cpu_count,
        memory=str(memory) if memory is not None else None,
        env=env,
        binds=binds,
        result_path=result_path,
        leader_selection=bool(raw.get("leaderSelection", False)),
    )


def _resolve_image(
    raw: Any,
    location: str,
    profile: SlurmProfile,
    image_map: Mapping[str, str],
    warnings: list[str],
) -> str:
    if not isinstance(raw, dict):
        raise TranslationError(f"{location}.image must be a mapping")
    choices = [(kind, raw[kind]) for kind in ("beaker", "docker") if kind in raw]
    if len(choices) != 1 or not isinstance(choices[0][1], str):
        raise TranslationError(f"{location}.image needs exactly one string field: beaker or docker")
    kind, reference = choices[0]
    _warn_unknown(raw, {"beaker", "docker"}, f"{location}.image", warnings)
    mapped = image_map.get(reference) or image_map.get(f"{kind}:{reference}")
    if mapped:
        return mapped
    if kind == "docker":
        return reference if "://" in reference else f"docker://{reference}"
    fallback = str(Path(profile.image_dir) / f"{_safe(reference)}.sif")
    warnings.append(f"unmapped Beaker image {reference!r} for {location}; expecting prebuilt SIF at {fallback}")
    return fallback


def _parse_env(raw: Any, location: str, warnings: list[str]) -> tuple[EnvironmentValue, ...]:
    if not isinstance(raw, (list, tuple)):
        raise TranslationError(f"{location}.envVars must be a list")
    result: list[EnvironmentValue] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TranslationError(f"{location}.envVars[{index}] must be a mapping")
        _warn_unknown(item, {"name", "value", "secret"}, f"{location}.envVars[{index}]", warnings)
        name = item.get("name")
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise TranslationError(f"{location}.envVars[{index}].name is not a valid environment variable")
        has_value = "value" in item
        has_secret = "secret" in item
        if has_value == has_secret:
            raise TranslationError(f"{location}.envVars[{index}] needs exactly one of value or secret")
        if has_secret:
            secret = item["secret"]
            if not isinstance(secret, str) or not _ENV_NAME.fullmatch(secret):
                raise TranslationError(f"{location}.envVars[{index}].secret must be a shell-safe secret name")
            result.append(EnvironmentValue(name=name, secret=secret))
        else:
            result.append(EnvironmentValue(name=name, value=str(item["value"])))
    return tuple(result)


def _parse_datasets(
    raw: Any,
    location: str,
    profile: SlurmProfile,
    dataset_map: Mapping[str, str],
    warnings: list[str],
) -> tuple[Bind, ...]:
    if not isinstance(raw, (list, tuple)):
        raise TranslationError(f"{location}.datasets must be a list")
    result: list[Bind] = []
    for index, item in enumerate(raw):
        label = f"{location}.datasets[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("source"), dict):
            raise TranslationError(f"{label} needs mountPath and source mappings")
        target = item.get("mountPath")
        if not isinstance(target, str) or not target.startswith("/"):
            raise TranslationError(f"{label}.mountPath must be an absolute path")
        source = item["source"]
        known = [(kind, source[kind]) for kind in ("weka", "hostPath", "beaker") if kind in source]
        _warn_unknown(item, {"mountPath", "source", "readOnly"}, label, warnings)
        if len(known) != 1 or not isinstance(known[0][1], str):
            warnings.append(f"ignored unsupported dataset source for {label}: {sorted(source)}")
            continue
        _warn_unknown(source, {"weka", "hostPath", "beaker"}, f"{label}.source", warnings)
        kind, reference = known[0]
        key = f"{kind}:{reference}"
        if kind != "hostPath" and key in profile.bundled_datasets:
            continue
        host_path = dataset_map.get(key) or dataset_map.get(reference)
        if host_path is None and kind == "hostPath":
            host_path = reference
        elif host_path is None and kind == "weka":
            host_path = target if target == "/weka" else str(Path("/weka") / reference)
            warnings.append(f"unmapped Weka dataset {reference!r}; binding {host_path} to {target}")
        elif host_path is None:
            host_path = str(Path(profile.dataset_root) / "beaker" / _safe(reference))
            warnings.append(f"unmapped Beaker dataset {reference!r}; expecting it at {host_path}")
        result.append(Bind(source=host_path, target=target, read_only=bool(item.get("readOnly", False))))
    return tuple(result)


def _headers(
    name: str,
    *,
    nodes: int,
    tasks: int,
    gpus: int,
    cpus: int,
    memory: str | None,
    partition: str | None,
    account: str | None,
    qos: str | None,
    profile: SlurmProfile,
) -> list[str]:
    directives: list[tuple[str, Any]] = [
        ("job-name", name),
        ("output", "slurm-%x-%j.out"),
        ("nodes", nodes),
        ("ntasks", tasks),
        ("cpus-per-task", cpus),
        ("time", profile.time_limit),
    ]
    if profile.tasks_per_node > 1 and not profile.completion_task:
        directives.append(("ntasks-per-node", profile.tasks_per_node))
    if gpus:
        directives.append(("gpus-per-node", gpus))
    directives.extend((("mem", memory), ("partition", partition), ("account", account), ("qos", qos)))
    directives.extend(profile.sbatch.items())
    lines = ["#!/usr/bin/env bash"]
    for key, value in directives:
        if value is None or value is False:
            continue
        lines.append(f"#SBATCH --{key}" if value is True else f"#SBATCH --{key}={value}")
    return lines


def _runtime_preamble(profile: SlurmProfile, nodes: int) -> list[str]:
    lines = [
        "",
        f"REXS_APPTAINER=${{REXS_APPTAINER:-{shlex.quote(profile.apptainer_binary)}}}",
        f"REXS_IMAGE_CACHE=${{REXS_IMAGE_CACHE:-{shlex.quote(profile.image_cache)}}}",
        f"REXS_RUN_ROOT=${{REXS_RUN_ROOT:-{shlex.quote(profile.run_root)}}}",
        'REXS_RUN_DIR="$REXS_RUN_ROOT/${SLURM_JOB_ID:-manual}"',
        'mkdir -p "$REXS_IMAGE_CACHE" "$REXS_RUN_DIR/logs" "$REXS_RUN_DIR/results"',
        'command -v "$REXS_APPTAINER" >/dev/null',
        "command -v srun >/dev/null",
        "command -v scontrol >/dev/null",
    ]
    if profile.secret_file:
        quoted = shlex.quote(profile.secret_file)
        lines.extend(
            [
                f"REXS_SECRETS_FILE=${{REXS_SECRETS_FILE:-{quoted}}}",
                'if [[ -f "$REXS_SECRETS_FILE" ]]; then',
                "  set -a",
                '  source "$REXS_SECRETS_FILE"',
                "  set +a",
                "fi",
            ]
        )
    lines.extend(profile.setup_commands)
    lines.extend(
        [
            'mapfile -t REXS_NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")',
            f"if (( ${{#REXS_NODES[@]}} < {nodes} )); then",
            f'  echo "expected {nodes} allocated nodes, got ${{#REXS_NODES[@]}}" >&2',
            "  exit 2",
            "fi",
            "REXS_PIDS=()",
            "REXS_CLEANED_UP=0",
            "cleanup() {",
            "  local pid",
            "  (( REXS_CLEANED_UP )) && return 0",
            "  REXS_CLEANED_UP=1",
            '  for pid in "${REXS_PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done',
            '  for pid in "${REXS_PIDS[@]:-}"; do wait "$pid" 2>/dev/null || true; done',
            "}",
            "trap cleanup EXIT INT TERM",
            "",
            "prepare_image() {",
            "  local source=$1 alias=$2 target temporary",
            '  if [[ "$source" != *://* ]]; then',
            '    [[ -f "$source" ]] || { echo "missing Apptainer image: $source" >&2; return 2; }',
            "    printf '%s\\n' \"$source\"",
            "    return 0",
            "  fi",
            '  target="$REXS_IMAGE_CACHE/$alias.sif"',
            '  if [[ ! -f "$target" ]]; then',
            '    temporary="$target.partial.$$"',
            '    "$REXS_APPTAINER" pull "$temporary" "$source"',
            '    mv "$temporary" "$target"',
            "  fi",
            "  printf '%s\\n' \"$target\"",
            "}",
        ]
    )
    return lines


def _image_preamble(plans: Sequence[TaskPlan], profile: SlurmProfile) -> list[str]:
    del profile
    lines = [""]
    resolved: dict[str, int] = {}
    for task in plans:
        if task.image in resolved:
            continue
        index = len(resolved)
        resolved[task.image] = index
        lines.append(f"REXS_IMAGE_{index}=$(prepare_image {shlex.quote(task.image)} {shlex.quote(_safe(task.image))})")
    return lines


def _task_launches(plans: Sequence[TaskPlan], profile: SlurmProfile) -> list[str]:
    lines: list[str] = [""]
    image_indexes: dict[str, int] = {}
    node_index = 0
    for task in plans:
        image_index = image_indexes.setdefault(task.image, len(image_indexes))
        leader_index = node_index // profile.tasks_per_node
        for rank in range(task.replicas):
            function = f"launch_{_safe(task.name).replace('.', '_').replace('-', '_')}_{rank}"
            result_dir = f"$REXS_RUN_DIR/results/{_safe(task.name)}/{rank}"
            log_path = f"$REXS_RUN_DIR/logs/{_safe(task.name)}.{rank}.log"
            lines.extend(
                [
                    f"{function}() {{",
                    f'  local node="${{REXS_NODES[{node_index // profile.tasks_per_node}]}}"',
                    "  (",
                    f"    export APPTAINERENV_BEAKER_REPLICA_RANK={rank}",
                    f"    export APPTAINERENV_BEAKER_REPLICA_COUNT={task.replicas}",
                    f'    export APPTAINERENV_BEAKER_LEADER_REPLICA_HOSTNAME="${{REXS_NODES[{leader_index}]}}"',
                    '    export APPTAINERENV_BEAKER_NODE_HOSTNAME="$node"',
                    '    export APPTAINERENV_BEAKER_JOB_ID="${SLURM_JOB_ID:-manual}"',
                    f"    export APPTAINERENV_REXS_TASK_NAME={shlex.quote(task.name)}",
                ]
            )
            for env in task.env:
                target = f"APPTAINERENV_{env.name}"
                if env.secret:
                    lines.extend(
                        [
                            f"    if [[ ! -v {env.secret} ]]; then",
                            f"      echo {shlex.quote(f'missing secret {env.secret} required as {env.name}')} >&2",
                            "      exit 2",
                            "    fi",
                            f'    export {target}="${{{env.secret}}}"',
                        ]
                    )
                else:
                    lines.append(f"    export {target}={shlex.quote(env.value or '')}")
            lines.append(f'    mkdir -p "{result_dir}"')
            apptainer_args = ["exec", "--cleanenv"]
            if task.gpu_count:
                apptainer_args.append("--nv")
            bind_values = [f"{bind.source}:{bind.target}{':ro' if bind.read_only else ''}" for bind in task.binds]
            if task.result_path:
                bind_values.append(f"{result_dir}:{task.result_path}")
            dynamic_binds: list[str] = []
            for bind in bind_values:
                if bind.startswith("$"):
                    dynamic_binds.append(bind)
                else:
                    apptainer_args.extend(("--bind", bind))
            lines.append(f"    APPTAINER_ARGS=({_shell_array(apptainer_args)})")
            for bind in dynamic_binds:
                lines.append(f'    APPTAINER_ARGS+=(--bind "{bind}")')
            srun_args = [
                "srun",
                "--nodes=1",
                "--ntasks=1",
                "--overlap" if profile.shared_cpus_per_node is not None and not task.gpu_count else "--exclusive",
                "--exact",
                f"--cpus-per-task={(profile.shared_cpus_per_node or task.cpu_count) if not task.gpu_count else task.cpu_count}",
            ]
            if profile.shared_cpus_per_node is not None:
                srun_args.append("--cpu-bind=none")
            if task.gpu_count:
                # Exclusive GPU steps let Slurm allocate disjoint GRES. Only
                # CPU-only service steps overlap, explicitly requesting no GRES.
                srun_args.extend((f"--gpus-per-task={task.gpu_count}", f"--gpus-per-node={task.gpu_count}"))
            elif profile.tasks_per_node > 1:
                # A site may reserve a GPU in SBATCH for CPU-only services.
                # Do not let concurrent steps inherit that exclusive GRES.
                srun_args.append("--gres=none")
            if task.memory:
                srun_args.append(f"--mem={_slurm_memory(task.memory)}")
            command = _shell_array(task.command)
            lines.extend(
                [
                    (
                        "    "
                        f'{_shell_array(srun_args)} --nodelist="$node" '
                        f'"$REXS_APPTAINER" "${{APPTAINER_ARGS[@]}}" '
                        f'"$REXS_IMAGE_{image_index}" {command}'
                    ),
                    f'  ) >"{log_path}" 2>&1 &',
                    '  REXS_PIDS+=("$!")',
                    "}",
                    f"{function}",
                    "",
                ]
            )
            if task.name == profile.completion_task:
                lines.append("REXS_COMPLETION_PID=${REXS_PIDS[-1]}")
            node_index += 1
    return lines


def _supervisor(completion_task: str | None = None) -> list[str]:
    if completion_task:
        return [
            'echo "launched ${#REXS_PIDS[@]} task replicas; logs: $REXS_RUN_DIR/logs"',
            "set +e",
            'wait -n -p finished_pid "${REXS_PIDS[@]}"',
            "exit_code=$?",
            "set -e",
            'if [[ ${finished_pid:-} != "$REXS_COMPLETION_PID" ]]; then',
            '  echo "service replica exited before training; stopping allocation" >&2',
            "  (( exit_code != 0 )) || exit_code=1",
            "fi",
            "cleanup",
            "trap - EXIT INT TERM",
            'exit "$exit_code"',
        ]
    return [
        'echo "launched ${#REXS_PIDS[@]} task replicas; logs: $REXS_RUN_DIR/logs"',
        "remaining=${#REXS_PIDS[@]}",
        "exit_code=0",
        "while (( remaining > 0 )); do",
        "  set +e",
        "  wait -n",
        "  child_status=$?",
        "  set -e",
        "  (( remaining -= 1 )) || true",
        "  if (( child_status != 0 )); then",
        '    echo "a task replica failed with status $child_status; terminating sibling steps" >&2',
        "    exit_code=$child_status",
        "    break",
        "  fi",
        "done",
        "cleanup",
        "trap - EXIT INT TERM",
        'exit "$exit_code"',
    ]


def _largest_memory(plans: Sequence[TaskPlan], warnings: list[str]) -> str | None:
    parsed: list[tuple[int, str]] = []
    for task in plans:
        if task.memory is None:
            continue
        try:
            parsed.append((_memory_mib(task.memory), _slurm_memory(task.memory)))
        except ValueError:
            warnings.append(f"could not normalize memory {task.memory!r} for task {task.name!r}; using profile default")
    return max(parsed)[1] if parsed else None


def _memory_mib(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]i?B?|[kmgt])?\s*", str(value))
    if not match:
        raise ValueError(value)
    number = float(match.group(1))
    unit = (match.group(2) or "M").upper().replace("IB", "").replace("B", "")
    powers = {"K": -1, "M": 0, "G": 1, "T": 2}
    return math.ceil(number * (1024 ** powers[unit]))


def _slurm_memory(value: str) -> str:
    mib = _memory_mib(value)
    if mib % 1024 == 0:
        return f"{mib // 1024}G"
    return f"{mib}M"


def _positive_int(value: Any, label: str) -> int:
    result = _int(value, label)
    if result < 1:
        raise TranslationError(f"{label} must be positive")
    return result


def _nonnegative_int(value: Any, label: str) -> int:
    result = _int(value, label)
    if result < 0:
        raise TranslationError(f"{label} must be non-negative")
    return result


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise TranslationError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        hint = (
            " (render template variables with --set KEY=VALUE first)"
            if isinstance(value, str) and "${" in value
            else ""
        )
        raise TranslationError(f"{label} must be an integer, got {value!r}{hint}") from exc
    if str(result) != str(value) and not isinstance(value, int):
        raise TranslationError(f"{label} must be an integer, got {value!r}")
    return result


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _safe(value: str) -> str:
    return _SAFE_NAME.sub("__", value).strip("._-")


def _shell_array(values: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(value)) for value in values)


def _warn_unknown(raw: Mapping[str, Any], known: set[str], location: str, warnings: list[str]) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        warnings.append(f"ignored unsupported fields in {location}: {', '.join(unknown)}")
