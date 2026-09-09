from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import fire
import yaml

from rexs.compiler import CompileResult, compile_experiment
from rexs.config import SlurmProfile, load_experiment, load_mapping_file, load_profile, parse_assignments
from rexs.controller import Controller
from rexs.dryrun import dry_run_experiments
from rexs.errors import RexsError
from rexs.server import serve, start_daemon, stop_daemon
from rexs.state import StateStore


class Rexs:
    """Reproducible Experiments, eXecuted on Slurm."""

    def validate(
        self,
        experiment: str,
        profile: str | None = None,
        image_map: str | None = None,
        dataset_map: str | None = None,
        define: Any = None,
        name: str | None = None,
        strict: bool = False,
    ) -> dict[str, Any]:
        """Validate and summarize a Beaker v2 experiment without writing or submitting."""

        _, _, result = _compile_inputs(experiment, profile, image_map, dataset_map, define, name)
        _check_and_report(result, strict)
        return {
            "valid": True,
            "job": result.job_name,
            "nodes": result.nodes,
            "task_replicas": result.tasks,
            "gpus_per_node": result.gpus_per_node,
            "warnings": list(result.warnings),
        }

    def dry_run(
        self,
        experiments: Any = "~/datadev/beaker_experiments",
        profile: str | None = None,
        image_map: str | None = None,
        dataset_map: str | None = None,
        define: Any = None,
        recursive: bool = True,
        strict: bool = False,
        output_dir: str | None = None,
        limit: int | None = None,
        details: bool = False,
    ) -> dict[str, Any]:
        """Offline compile and bash-syntax audit; never invokes Slurm."""

        report = dry_run_experiments(
            _string_list(experiments),
            profile=load_profile(profile),
            image_map=load_mapping_file(image_map, "image map"),
            dataset_map=load_mapping_file(dataset_map, "dataset map"),
            substitutions=_definitions(define),
            recursive=recursive,
            strict=strict,
            output_dir=output_dir,
            limit=limit,
        )
        if strict and report["failed"]:
            failures = report["failures"]
            sample = "\n".join(f"- {failure['path']}: {failure['error']}" for failure in failures[:10])
            remaining = len(failures) - 10
            suffix = f"\n- ... and {remaining} more" if remaining > 0 else ""
            raise RexsError(f"strict dry run failed {report['failed']} experiment(s):\n{sample}{suffix}")
        if not details:
            report.pop("results")
        return report

    def render(
        self,
        experiment: str,
        output: str | None = None,
        profile: str | None = None,
        image_map: str | None = None,
        dataset_map: str | None = None,
        define: Any = None,
        name: str | None = None,
        strict: bool = False,
    ) -> str:
        """Render an sbatch script to stdout or --output."""

        _, _, result = _compile_inputs(experiment, profile, image_map, dataset_map, define, name)
        _check_and_report(result, strict)
        if output is None:
            return result.script
        target = Path(output).expanduser()
        _write_script(target, result.script)
        return str(target)

    def submit(
        self,
        experiment: str,
        output: str | None = None,
        profile: str | None = None,
        image_map: str | None = None,
        dataset_map: str | None = None,
        define: Any = None,
        name: str | None = None,
        strict: bool = False,
        sbatch_args: Any = None,
        db: str | None = None,
    ) -> dict[str, Any]:
        """Compile, persist, submit with sbatch, and register the experiment in SQLite."""

        spec, slurm_profile, result = _compile_inputs(
            experiment,
            profile,
            image_map,
            dataset_map,
            define,
            name,
        )
        _check_and_report(result, strict)
        store = StateStore(db)
        source = Path(experiment).expanduser().resolve()
        spec_text = source.read_text(encoding="utf-8")
        script_hash = _sha256(result.script)
        target = (
            Path(output).expanduser()
            if output
            else store.path.parent / "artifacts" / f"{_safe(result.job_name)}-{script_hash[:12]}.sbatch"
        )
        _write_script(target, result.script)
        record = store.create_experiment(
            spec_text=spec_text,
            name=result.job_name,
            spec_path=str(source),
            spec_sha256=_sha256(spec_text),
            script_path=str(target.resolve()),
            script_sha256=script_hash,
            run_root=slurm_profile.run_root,
            warnings=result.warnings,
            tasks=spec["tasks"],
        )
        command = ["sbatch", *_string_list(sbatch_args), str(target)]
        try:
            completed = subprocess.run(command, check=True, text=True, capture_output=True)
            job_id = _job_id(completed.stdout)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            detail = _command_error(exc)
            store.record_submission_failure(record.id, detail)
            raise RexsError(detail) from exc
        submitted = store.record_submission(record.id, job_id, completed.stdout)
        return submitted.as_dict()

    def experiments(
        self,
        status: str | None = None,
        limit: int = 100,
        refresh: bool = True,
        db: str | None = None,
        details: bool = False,
        ids_only: bool = False,
    ) -> str | list[dict[str, Any]]:
        """List ID, status and name. Use --details for JSON or --ids-only for IDs."""

        controller = Controller(db)
        if refresh:
            controller.refresh()
        records = controller.store.list(status=status, limit=limit)
        if ids_only:
            return "\n".join(item.job_id or item.id for item in records)
        if details:
            return [item.as_dict() for item in records]
        if not records:
            return "No experiments found."
        rows = [("ID", "STATUS", "NAME")]
        rows.extend(
            (item.job_id or item.id, item.status, " ".join(item.name.split())[:60])
            for item in records
        )
        id_width = max(len(row[0]) for row in rows)
        status_width = max(len(row[1]) for row in rows)
        return "\n".join(f"{identifier:<{id_width}}  {state:<{status_width}}  {name}"
                         for identifier, state, name in rows)

    def show(self, identifier: str, refresh: bool = True, db: str | None = None) -> dict[str, Any]:
        """Show one experiment by REXS ID or Slurm job ID."""

        controller = Controller(db)
        if refresh:
            controller.refresh(identifier)
        experiment = controller.store.get(identifier)
        return {
            "experiment": experiment.as_dict(include_spec=True),
            "tasks": [task.as_dict() for task in controller.store.tasks(identifier)],
            "events": controller.store.events(identifier),
        }

    def status(self, identifier: str, refresh: bool = True, db: str | None = None) -> dict[str, Any]:
        """Return the durable status of one tracked experiment."""

        return self.show(identifier, refresh=refresh, db=db)["experiment"]

    def refresh(self, identifier: str | None = None, db: str | None = None) -> list[dict[str, Any]]:
        """Refresh tracked state from squeue and sacct."""

        return [asdict(item) for item in Controller(db).refresh(identifier)]

    def logs(
        self,
        identifier: str,
        task: str | None = None,
        replica: int | None = None,
        lines: int = 200,
        follow: bool = False,
        db: str | None = None,
    ) -> list[dict[str, object]] | None:
        """Read or follow per-replica logs for an experiment."""

        controller = Controller(db)
        records = controller.logs(identifier, task=task, replica=replica, lines=lines)
        if not follow:
            return records
        paths = [str(record["log_path"]) for record in records if record["exists"]]
        if not paths:
            raise RexsError("no matching log files exist yet")
        subprocess.run(["tail", "-n", str(lines), "-F", *paths], check=False)
        return None

    def cancel(self, identifier: str, db: str | None = None) -> dict[str, Any]:
        """Cancel a tracked Slurm experiment and record the transition."""

        return Controller(db).cancel(identifier).as_dict()

    def server(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        db: str | None = None,
        poll_interval: float = 10.0,
        daemon: bool = False,
    ) -> dict[str, Any] | None:
        """Run the dashboard/API server, optionally detached with --daemon."""

        if daemon:
            return start_daemon(host=host, port=port, db_path=db, poll_interval=poll_interval)
        serve(host=host, port=port, db_path=db, poll_interval=poll_interval)
        return None

    def server_stop(self, db: str | None = None) -> dict[str, Any]:
        """Stop the detached server associated with this state database."""

        return stop_daemon(db)


def main(argv: Sequence[str] | None = None) -> None:
    try:
        fire.Fire(Rexs(), command=list(argv) if argv is not None else None,
                  serialize=lambda value: json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value))
    except (RexsError, KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def _compile_inputs(
    experiment: str,
    profile_path: str | None,
    image_map_path: str | None,
    dataset_map_path: str | None,
    define: Any,
    name: str | None,
) -> tuple[dict[str, Any], SlurmProfile, CompileResult]:
    source = Path(experiment).expanduser()
    profile = load_profile(profile_path)
    image_map = load_mapping_file(image_map_path, "image map")
    dataset_map = load_mapping_file(dataset_map_path, "dataset map")
    spec = load_experiment(source, _definitions(define))
    job_name = name or str(spec.get("name") or source.stem)
    result = compile_experiment(
        spec,
        profile=profile,
        job_name=job_name,
        image_map=image_map,
        dataset_map=dataset_map,
    )
    return spec, profile, result


def _definitions(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, str) and value.lstrip().startswith("{"):
        parsed = yaml.safe_load(value)
        if not isinstance(parsed, Mapping):
            raise ValueError("--define mapping must contain key/value pairs")
        return {str(key): str(item) for key, item in parsed.items()}
    return parse_assignments(_string_list(value))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _check_and_report(result: CompileResult, strict: bool) -> None:
    if strict and result.warnings:
        raise RexsError("strict translation refused warnings:\n- " + "\n- ".join(result.warnings))
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _write_script(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)


def _job_id(output: str) -> str:
    match = re.search(r"Submitted\s+batch\s+job\s+(\d+)", output, re.IGNORECASE)
    if not match:
        raise ValueError(f"could not parse Slurm job ID from sbatch output: {output.strip()!r}")
    return match.group(1)


def _command_error(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return (exc.stderr or exc.stdout or str(exc)).strip()
    return str(exc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", value).strip("._-") or "experiment"


if __name__ == "__main__":
    main()
