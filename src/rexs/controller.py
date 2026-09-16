from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from rexs.state import TERMINAL_STATES, ExperimentRecord, StateStore

logger = logging.getLogger(__name__)

_SLURM_STATUS = {
    "PENDING": "PENDING",
    "CONFIGURING": "PENDING",
    "RUNNING": "RUNNING",
    "COMPLETING": "RUNNING",
    "SUSPENDED": "SUSPENDED",
    "COMPLETED": "COMPLETED",
    "CANCELLED": "CANCELLED",
    "FAILED": "FAILED",
    "TIMEOUT": "TIMEOUT",
    "NODE_FAIL": "NODE_FAIL",
    "OUT_OF_MEMORY": "OUT_OF_MEMORY",
    "PREEMPTED": "PREEMPTED",
    "BOOT_FAIL": "FAILED",
    "DEADLINE": "FAILED",
    "REVOKED": "CANCELLED",
}


@dataclass(frozen=True)
class RefreshResult:
    experiment_id: str
    job_id: str
    previous_status: str
    status: str
    exit_code: str | None = None


class Controller:
    """Piggy-back on Slurm for execution while retaining durable REXS state."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.store = StateStore(db_path)
        self.runner = runner or subprocess.run

    def refresh(self, identifier: str | None = None) -> list[RefreshResult]:
        experiments = [self.store.get(identifier)] if identifier else self.store.active()
        updates: list[RefreshResult] = []
        for experiment in experiments:
            if experiment.allocations:
                try:
                    updates.extend(self._refresh_allocations(experiment))
                except (OSError, ValueError, subprocess.SubprocessError):
                    if identifier:
                        raise
                    logger.warning("Could not refresh grouped experiment %s", experiment.id, exc_info=True)
                continue
            if not experiment.job_id or experiment.status in TERMINAL_STATES:
                continue
            try:
                status, exit_code, detail = self._slurm_status(experiment.job_id)
            except (OSError, subprocess.SubprocessError):
                if identifier:
                    raise
                logger.warning(
                    "Could not refresh Slurm job %s; continuing with other jobs", experiment.job_id, exc_info=True
                )
                continue
            if status != experiment.status or exit_code:
                self.store.update_status(experiment.id, status, detail=detail, exit_code=exit_code)
            updates.append(
                RefreshResult(
                    experiment_id=experiment.id,
                    job_id=experiment.job_id,
                    previous_status=experiment.status,
                    status=status,
                    exit_code=exit_code,
                )
            )
        try:
            self.refresh_runtimes()
        except (OSError, subprocess.SubprocessError):
            logger.warning("Could not refresh elapsed runtimes; retaining saved values", exc_info=True)
        try:
            self.refresh_start_estimates()
        except (OSError, subprocess.SubprocessError):
            logger.warning("Could not refresh scheduled start estimates", exc_info=True)
        return updates

    @contextmanager
    def _experiment_lock(self, experiment_id):
        # Serialize refresh/restart and cancel across server and CLI processes.
        path = self.store.path.parent / f".{self.store.path.name}.{experiment_id}.lock"
        with path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _refresh_allocations(self, experiment):
        with self._experiment_lock(experiment.id):
            experiment = self.store.get(experiment.id)
            if experiment.status in TERMINAL_STATES | {"GENERATED"}:
                return []
            return self._refresh_allocations_locked(experiment)

    def _refresh_allocations_locked(self, experiment):
        updates = []
        for allocation in experiment.allocations:
            if not allocation["job_id"] or allocation["status"] in TERMINAL_STATES | {"RESTARTING"}:
                continue
            try:
                status, exit_code, detail = self._slurm_status(allocation["job_id"])
            except (OSError, subprocess.SubprocessError):
                logger.warning("Could not refresh allocation %s", allocation["name"], exc_info=True)
                continue
            if status != allocation["status"] or exit_code:
                self.store.update_allocation(
                    experiment.id, allocation["name"], status, exit_code=exit_code, detail=detail
                )
            updates.append(RefreshResult(experiment.id, allocation["job_id"], allocation["status"], status, exit_code))
        allocations = self.store.allocations(experiment.id)
        completion = next((a for a in allocations if a["completion"]), None)
        if completion and completion["status"] in TERMINAL_STATES:
            # Cancellation of services is normal when the completion task exits.
            self._cancel_allocations(experiment.id, allocations)
            self.store.update_status(
                experiment.id, completion["status"], exit_code=completion["exit_code"], detail="Completion task exited"
            )
        else:
            if self._apply_failure_policies(experiment, allocations):
                return updates
            allocations = self.store.allocations(experiment.id)
            states = [a["status"] for a in allocations]
            if all(state in TERMINAL_STATES for state in states):
                failures = [state for state in states if state not in ("COMPLETED", "CANCELLED")]
                status = failures[0] if failures else ("CANCELLED" if "CANCELLED" in states else "COMPLETED")
            elif "RUNNING" in states:
                status = "RUNNING"
            elif any(state in ("PENDING", "SUBMITTED") for state in states):
                status = "PENDING"
            else:
                status = "UNKNOWN"
            self.store.update_status(experiment.id, status, detail="Aggregate allocation status")
        return updates

    def _apply_failure_policies(self, experiment, allocations):
        policies = (yaml.safe_load(experiment.spec_text) or {}).get("rexs", {}).get("failure_policies", {})
        failures = []
        restart = []
        for allocation in allocations:
            policy = policies.get(allocation["tasks"][0]["name"], {})
            action = policy.get("action", "continue")
            if allocation["status"] == "RESTARTING":
                raise ValueError(f"{allocation['name']}: interrupted restart; inspect scheduler before retrying")
            elif allocation["status"] in TERMINAL_STATES - {"COMPLETED"}:
                if action == "fail_experiment":
                    failures.append(f"{allocation['name']}: {allocation['status']}")
                elif action == "restart":
                    if len(allocation["attempts"]) >= policy["max_restarts"]:
                        failures.append(f"{allocation['name']}: restart budget exhausted")
                    else:
                        restart.append(allocation)
        if failures:
            self._cancel_allocations(experiment.id, allocations)
            self.store.update_status(experiment.id, "FAILED", detail="; ".join(failures))
            return True
        for allocation in restart:
            self._restart_allocation(experiment.id, allocation)
        return False

    def _restart_allocation(self, experiment_id, allocation):
        script = Path(allocation["script_path"])
        if hashlib.sha256(script.read_bytes()).hexdigest() != allocation["script_sha256"]:
            raise ValueError(f"Refusing modified restart script: {script}")
        # Persist intent before sbatch. An interrupted submission is never blindly retried.
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO allocation_attempts VALUES (?,?,?,?,?,?)",
                (
                    experiment_id,
                    allocation["name"],
                    len(allocation["attempts"]),
                    allocation["job_id"],
                    allocation["status"],
                    allocation["exit_code"],
                ),
            )
            db.execute(
                "UPDATE allocations SET status='RESTARTING' WHERE experiment_id=? AND name=?",
                (experiment_id, allocation["name"]),
            )
        try:
            result = self.runner(["sbatch", str(script)], check=True, text=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as error:
            self.store.update_allocation(
                experiment_id,
                allocation["name"],
                "SUBMISSION_FAILED",
                detail=f"Replacement submission rejected: {error}",
            )
            raise
        job_id = parse_job_id(result.stdout)
        with (script.parent / "owned-jobs.txt").open("a") as owned:
            owned.write(job_id + "\n")
        with self.store.connect() as db:
            db.execute(
                "UPDATE allocations SET exit_code=NULL WHERE experiment_id=? AND name=?",
                (experiment_id, allocation["name"]),
            )
        self.store.update_allocation(
            experiment_id,
            allocation["name"],
            "SUBMITTED",
            job_id=job_id,
            detail=f"Replacement {job_id} for {allocation['job_id']}",
        )

    def _cancel_allocations(self, experiment_id, allocations):
        active = [a for a in allocations if a["job_id"] and a["status"] not in TERMINAL_STATES | {"RESTARTING"}]
        if active:
            self.runner(["scancel", *[a["job_id"] for a in active]], check=True, text=True, capture_output=True)
            for allocation in active:
                self.store.update_allocation(
                    experiment_id, allocation["name"], "CANCELLED", detail="Experiment cleanup"
                )

        if any(a["status"] == "RESTARTING" for a in allocations):
            raise ValueError(
                "Known jobs cancelled, but an interrupted replacement submission needs scheduler reconciliation"
            )

    def refresh_start_estimates(self):
        records = [item for item in self.store.active() if item.job_id and item.status in {"SUBMITTED", "PENDING"}]
        for start in range(0, len(records), 500):
            batch = records[start : start + 500]
            ids = {item.job_id: item.id for item in batch}
            result = self.runner(
                ["squeue", "--start", "--jobs", ",".join(ids), "--noheader", "--format=%i|%S"],
                check=True,
                text=True,
                capture_output=True,
                timeout=30,
                env={**os.environ, "TZ": "UTC", "SLURM_TIME_FORMAT": "standard"},
            )
            estimates = dict.fromkeys(ids)
            for line in result.stdout.splitlines():
                job, separator, value = line.strip().partition("|")
                if not separator or job not in ids:
                    continue
                try:
                    estimates[job] = datetime.fromisoformat(value).replace(tzinfo=UTC).isoformat()
                except ValueError:
                    pass
            with self.store.connect() as db:
                for job, estimate in estimates.items():
                    db.execute("UPDATE experiments SET estimated_start_at=? WHERE id=?", (estimate, ids[job]))

    def refresh_runtimes(self):
        records = [
            item
            for item in self.store.list(limit=10000)
            if item.job_id and (item.status not in TERMINAL_STATES or item.runtime_seconds is None)
        ]
        for start in range(0, len(records), 500):
            batch = records[start : start + 500]
            ids = {item.job_id: item.id for item in batch}
            result = self.runner(
                [
                    "sacct",
                    "--jobs",
                    ",".join(ids),
                    "--noheader",
                    "--parsable2",
                    "--allocations",
                    "--format=JobIDRaw,ElapsedRaw",
                ],
                check=True,
                text=True,
                capture_output=True,
                timeout=30,
            )
            with self.store.connect() as db:
                for line in result.stdout.splitlines():
                    fields = line.strip().split("|")
                    if len(fields) >= 2 and fields[0] in ids and fields[1].isdigit():
                        db.execute(
                            "UPDATE experiments SET runtime_seconds=? WHERE id=?", (int(fields[1]), ids[fields[0]])
                        )

    def cancel(self, identifier: str, *, source: str = "local controller/CLI") -> ExperimentRecord:
        experiment = self.store.get(identifier)
        if experiment.allocations:
            with self._experiment_lock(experiment.id):
                experiment = self.store.get(experiment.id)
                if experiment.status in TERMINAL_STATES:
                    return experiment
                self._cancel_allocations(experiment.id, experiment.allocations)
                return self.store.update_status(
                    experiment.id, "CANCELLED", detail=f"All experiment allocations cancelled; source={source}"
                )
        if not experiment.job_id:
            raise ValueError(f"experiment {experiment.id} has no submitted Slurm job")
        if experiment.status in TERMINAL_STATES:
            raise ValueError(f"experiment {experiment.id} is already terminal ({experiment.status})")
        self.runner(["scancel", experiment.job_id], check=True, text=True, capture_output=True)
        return self.store.update_status(
            experiment.id,
            "CANCELLED",
            detail=f"scancel requested for job {experiment.job_id}; source={source}",
        )

    def logs(
        self,
        identifier: str,
        *,
        task: str | None = None,
        replica: int | None = None,
        lines: int = 200,
    ) -> list[dict[str, object]]:
        if lines < 1:
            raise ValueError("lines must be positive")
        records = self.store.tasks(identifier)
        selected = [
            record
            for record in records
            if (task is None or record.name == task) and (replica is None or record.replica_rank == replica)
        ]
        output: list[dict[str, object]] = []
        for record in selected:
            path = Path(record.log_path) if record.log_path else None
            output.append(
                {
                    **record.as_dict(),
                    "exists": bool(path and path.is_file()),
                    "content": tail_text(path, lines) if path and path.is_file() else "",
                }
            )
        return output

    def _slurm_status(self, job_id: str) -> tuple[str, str | None, str | None]:
        queue_error = None
        try:
            current = self.runner(
                ["squeue", "--jobs", job_id, "--noheader", "--format=%T"],
                check=True,
                text=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            # Slurm can reject a completed job ID once it leaves the live queue.
            # Accounting remains authoritative even when squeue exits nonzero.
            queue_error = exc
            states = []
        else:
            states = [line.strip() for line in current.stdout.splitlines() if line.strip()]
        if states:
            state = _normalize_state(states[0])
            return state, None, f"squeue: {states[0]}"

        history = self.runner(
            ["sacct", "--jobs", job_id, "--noheader", "--parsable2", "--allocations", "--format=State,ExitCode"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        rows = [line.strip() for line in history.stdout.splitlines() if line.strip()]
        if not rows:
            if queue_error is not None:
                raise queue_error
            return "UNKNOWN", None, "job absent from squeue and sacct"
        state_text, _, exit_code = rows[0].partition("|")
        return _normalize_state(state_text), exit_code or None, f"sacct: {rows[0]}"


def parse_job_id(output: str) -> str:
    match = re.search(r"Submitted batch job\s+(\d+(?:_\d+)?)", output)
    if not match:
        raise ValueError(f"could not parse Slurm job id from sbatch output: {output.strip()!r}")
    return match.group(1)


def _normalize_state(value: str) -> str:
    canonical = value.strip().upper().split()[0].split("+")[0]
    return _SLURM_STATUS.get(canonical, "UNKNOWN")


def _tail(path: Path, lines: int) -> list[str]:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        position = stream.tell()
        chunks: list[bytes] = []
        newline_count = 0
        while position > 0 and newline_count <= lines:
            size = min(8192, position)
            position -= size
            stream.seek(position)
            chunk = stream.read(size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    return b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()[-lines:]


def tail_text(path: Path, lines: int) -> str:
    value = _tail(path, lines)
    return "\n".join(value)
