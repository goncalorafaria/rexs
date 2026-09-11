from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rexs.errors import ConfigurationError

_TEMPLATE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class SlurmProfile:
    """Site-specific Slurm and storage policy, separate from Beaker intent."""

    account: str | None = None
    partition: str | None = None
    qos: str | None = None
    time_limit: str = "24:00:00"
    cpus_per_task: int = 4
    tasks_per_node: int = 1
    completion_task: str | None = None
    shared_cpus_per_node: int | None = None
    memory: str | None = None
    apptainer_binary: str = "apptainer"
    image_cache: str = ".rexs/images"
    image_dir: str = "/weka/gfaria/apptainer/images"
    dataset_root: str = "/weka/gfaria/datasets"
    run_root: str = ".rexs/runs"
    secret_file: str | None = None
    setup_commands: tuple[str, ...] = ()
    sbatch: Mapping[str, str | int | bool | None] = field(default_factory=dict)
    images: Mapping[str, str] = field(default_factory=dict)
    datasets: Mapping[str, str] = field(default_factory=dict)
    bundled_datasets: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> SlurmProfile:
        raw = dict(value or {})
        known = {item.name for item in cls.__dataclass_fields__.values()}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ConfigurationError(f"unknown profile fields: {', '.join(unknown)}")
        if "setup_commands" in raw:
            raw["setup_commands"] = tuple(raw["setup_commands"] or ())
        if "bundled_datasets" in raw:
            if not isinstance(raw["bundled_datasets"], list) or not all(
                isinstance(item, str) for item in raw["bundled_datasets"]
            ):
                raise ConfigurationError("bundled_datasets must be a list of dataset references")
            raw["bundled_datasets"] = tuple(raw["bundled_datasets"])
        if isinstance(raw.get("time_limit"), int):
            raw["time_limit"] = _seconds_as_slurm_time(raw["time_limit"])
        profile = cls(**raw)
        if profile.cpus_per_task < 1:
            raise ConfigurationError("profile.cpus_per_task must be positive")
        if isinstance(profile.tasks_per_node, bool) or not isinstance(profile.tasks_per_node, int) or profile.tasks_per_node < 1:
            raise ConfigurationError("profile.tasks_per_node must be a positive integer")
        if profile.shared_cpus_per_node is not None:
            value = profile.shared_cpus_per_node
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigurationError("profile.shared_cpus_per_node must be a positive integer")
            if not profile.completion_task:
                raise ConfigurationError("shared_cpus_per_node requires completion_task")
        return profile


def load_profile(path: str | Path | None) -> SlurmProfile:
    if path is None:
        return SlurmProfile()
    value = _load_yaml_mapping(Path(path), "profile")
    return SlurmProfile.from_mapping(value)


def load_mapping_file(path: str | Path | None, label: str) -> dict[str, str]:
    if path is None:
        return {}
    value = _load_yaml_mapping(Path(path), label)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ConfigurationError(f"{label} must map strings to strings")
    return dict(value)


def load_experiment(path: str | Path, substitutions: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read experiment {source}: {exc}") from exc
    values = substitutions or {}
    text = _TEMPLATE.sub(lambda match: values.get(match.group(1), match.group(0)), text)
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid experiment YAML/JSON in {source}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError(f"experiment {source} must contain a mapping")
    return parsed


def parse_assignments(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ConfigurationError(f"template assignment must be KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigurationError(f"invalid template variable name {key!r}")
        result[key] = value
    return result


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read {label} {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid {label} YAML in {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} {path} must contain a mapping")
    return value


def _seconds_as_slurm_time(value: int) -> str:
    """Normalize PyYAML's legacy sexagesimal parsing of unquoted HH:MM:SS."""

    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
