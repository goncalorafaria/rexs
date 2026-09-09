from __future__ import annotations

import hashlib
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from rexs.compiler import compile_experiment
from rexs.config import SlurmProfile, load_experiment

EXPERIMENT_SUFFIXES = {".json", ".yaml", ".yml"}


@dataclass(frozen=True)
class DryRunResult:
    path: str
    passed: bool
    job_name: str | None = None
    nodes: int | None = None
    task_replicas: int | None = None
    gpus_per_node: int | None = None
    script_sha256: str | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    output: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_experiments(inputs: Sequence[str | Path], *, recursive: bool = True) -> list[Path]:
    """Find candidate Beaker v2 YAML/JSON files without following symlinks."""

    discovered: set[Path] = set()
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_file():
            discovered.add(path.resolve())
            continue
        if not path.is_dir():
            raise FileNotFoundError(f"dry-run input does not exist: {path}")
        iterator = path.rglob("*") if recursive else path.glob("*")
        for candidate in iterator:
            if candidate.is_file() and candidate.suffix.lower() in EXPERIMENT_SUFFIXES:
                discovered.add(candidate.resolve())
    return sorted(discovered)


def dry_run_experiments(
    inputs: Sequence[str | Path],
    *,
    profile: SlurmProfile | None = None,
    image_map: Mapping[str, str] | None = None,
    dataset_map: Mapping[str, str] | None = None,
    substitutions: Mapping[str, str] | None = None,
    recursive: bool = True,
    strict: bool = False,
    output_dir: str | Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Compile and syntax-check Beaker specs without invoking any Slurm command."""

    candidates = discover_experiments(inputs, recursive=recursive)
    results: list[DryRunResult] = []
    skipped = 0
    destination = Path(output_dir).expanduser() if output_dir else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)

    for path in candidates:
        try:
            spec = load_experiment(path, substitutions)
        # Corpus discovery must report malformed and unreadable files without
        # aborting the remaining independent experiments.
        except Exception as exc:  # noqa: BLE001
            results.append(DryRunResult(path=str(path), passed=False, error=f"{type(exc).__name__}: {exc}"))
            continue
        if spec.get("version") != "v2" or not isinstance(spec.get("tasks"), list):
            skipped += 1
            continue
        if limit is not None and len(results) >= limit:
            break
        try:
            job_name = str(spec.get("name") or path.stem)
            compiled = compile_experiment(
                spec,
                profile=profile,
                job_name=job_name,
                image_map=image_map,
                dataset_map=dataset_map,
            )
            if strict and compiled.warnings:
                raise ValueError("strict dry run rejected translation warnings:\n- " + "\n- ".join(compiled.warnings))
            syntax = subprocess.run(
                ["bash", "-n"],
                input=compiled.script,
                check=False,
                text=True,
                capture_output=True,
            )
            if syntax.returncode:
                raise ValueError(f"generated shell failed bash -n: {syntax.stderr.strip()}")
            output = None
            if destination:
                target = destination / f"{_safe_filename(compiled.job_name)}.sbatch"
                target.write_text(compiled.script, encoding="utf-8")
                target.chmod(0o700)
                output = str(target.resolve())
            results.append(
                DryRunResult(
                    path=str(path),
                    passed=True,
                    job_name=compiled.job_name,
                    nodes=compiled.nodes,
                    task_replicas=compiled.tasks,
                    gpus_per_node=compiled.gpus_per_node,
                    script_sha256=hashlib.sha256(compiled.script.encode()).hexdigest(),
                    warnings=compiled.warnings,
                    output=output,
                )
            )
        # One compiler or shell-check failure is a result, not a corpus abort.
        except Exception as exc:  # noqa: BLE001
            results.append(DryRunResult(path=str(path), passed=False, error=f"{type(exc).__name__}: {exc}"))

    passed = sum(item.passed for item in results)
    failures = [item.as_dict() for item in results if not item.passed]
    warning_categories = Counter(_warning_category(warning) for item in results for warning in item.warnings)
    return {
        "inputs": [str(Path(item).expanduser()) for item in inputs],
        "candidates": len(candidates),
        "experiments": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "skipped_non_experiments": skipped,
        "warnings": sum(warning_categories.values()),
        "warning_categories": dict(warning_categories.most_common()),
        "failures": failures,
        "results": [item.as_dict() for item in results],
        "slurm_commands_invoked": False,
    }


def _warning_category(warning: str) -> str:
    if warning.startswith("unmapped Beaker image"):
        return "unmapped Beaker images"
    if warning.startswith("unmapped Beaker dataset"):
        return "unmapped Beaker datasets"
    if warning.startswith("unmapped Weka dataset"):
        return "unmapped Weka datasets"
    if warning.startswith("homogeneous allocation"):
        return "homogeneous allocation over-provisioning"
    if "sharedMemory" in warning:
        return "shared memory uses host IPC"
    if warning.startswith("ignored ") or " is ignored;" in warning:
        return "unsupported Beaker features"
    return "other translation warnings"


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "__" for character in value)
