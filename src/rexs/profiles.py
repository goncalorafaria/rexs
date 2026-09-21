"""Backend selection and native deployment policy."""

from dataclasses import dataclass, field
from pathlib import Path

from rexs.config import SlurmProfile, _load_yaml_mapping
from rexs.errors import ConfigurationError


@dataclass(frozen=True)
class BeakerProfile:
    settings: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)
    datasets: dict = field(default_factory=dict)
    backend: str = "beaker"


def load_execution_profile(path=None):
    raw = _load_yaml_mapping(Path(path).expanduser(), "profile") if path else {}
    backend = raw.get("backend", "slurm")
    if backend == "slurm":
        if "slurm" not in raw and "runtime" not in raw:
            return SlurmProfile.from_mapping({k: v for k, v in raw.items() if k != "backend"})
        unknown = set(raw) - {"backend", "slurm", "runtime", "images", "datasets"}
        if unknown:
            raise ConfigurationError(f"unknown Slurm profile fields: {sorted(unknown)}")
        runtime = raw.get("runtime", {})
        if not isinstance(runtime, dict) or set(runtime) - {"kind", "apptainer_binary", "image_cache", "image_dir"}:
            raise ConfigurationError("runtime supports kind, apptainer_binary, image_cache, image_dir")
        if runtime.get("kind", "apptainer") != "apptainer":
            raise ConfigurationError("Slurm currently supports only the apptainer runtime")
        settings = raw.get("slurm", {})
        if not isinstance(settings, dict):
            raise ConfigurationError("profile.slurm must be a mapping")
        settings = {**settings, **{k: v for k, v in runtime.items() if k != "kind"}}
        settings.update({k: raw[k] for k in ("images", "datasets") if k in raw})
        return SlurmProfile.from_mapping(settings)
    if backend != "beaker":
        raise ConfigurationError(f"unknown execution backend: {backend}")
    if set(raw) - {"backend", "beaker", "images", "datasets"}:
        raise ConfigurationError("Beaker profiles support backend, beaker, images, datasets")
    settings = raw.get("beaker", {})
    if not isinstance(settings, dict) or set(settings) - {"workspace", "budget", "clusters", "priority", "context"}:
        raise ConfigurationError("beaker supports workspace, budget, clusters, priority, context")
    for key in ("workspace", "budget", "priority"):
        if key in settings and (not isinstance(settings[key], str) or not settings[key]):
            raise ConfigurationError(f"beaker.{key} must be a non-empty string")
    if "clusters" in settings and (
        not isinstance(settings["clusters"], list)
        or not settings["clusters"]
        or not all(isinstance(x, str) and x for x in settings["clusters"])
    ):
        raise ConfigurationError("beaker.clusters must be a non-empty list of names")
    if not isinstance(settings.get("context", {}), dict):
        raise ConfigurationError("beaker.context must be a mapping")
    for key in ("images", "datasets"):
        if not isinstance(raw.get(key, {}), dict):
            raise ConfigurationError(f"profile.{key} must be a mapping")
    return BeakerProfile(settings, raw.get("images", {}), raw.get("datasets", {}))
