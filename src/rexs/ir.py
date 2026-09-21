"""Experiment intermediate representation, before backend resource resolution."""

from copy import deepcopy
from dataclasses import dataclass

from rexs.errors import ConfigurationError


@dataclass(frozen=True)
class Experiment:
    """Preserve task intent and native metadata without resolving images or mounts."""

    tasks: tuple[dict, ...]
    metadata: dict

    @classmethod
    def from_dict(cls, value):
        if value.get("version") not in ("v2", "rexs/v1"):
            raise ConfigurationError("experiment version must be v2 or rexs/v1")
        tasks = value.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ConfigurationError("experiment.tasks must be a non-empty list")
        names = set()
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise ConfigurationError("each task must be a mapping")
            name = task.get("name", f"task-{index}")
            if not isinstance(name, str) or not name or name in names:
                raise ConfigurationError("task names must be non-empty and unique")
            names.add(name)
        return cls(tuple(deepcopy(tasks)), deepcopy({k: v for k, v in value.items() if k not in ("tasks", "version")}))

    def to_dict(self):
        return {"version": "rexs/v1", **deepcopy(self.metadata), "tasks": deepcopy(list(self.tasks))}

    def to_beaker(self):
        return {**self.to_dict(), "version": "v2"}

    def for_slurm(self):
        spec = self.to_beaker()
        for task in spec["tasks"]:
            if isinstance(task.get("image"), str):
                task["image"] = {"beaker": task["image"]}
            for mount in task.get("datasets", []):
                source = mount.get("source", {})
                if isinstance(source, dict) and set(source) == {"ref"}:
                    mount["source"] = {"beaker": source["ref"]}
        return spec
