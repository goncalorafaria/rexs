from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rexs.state import TERMINAL_STATES, ExperimentRecord, StateStore

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
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.store = StateStore(db_path)
        self.runner = runner

    def refresh(self, identifier: str | None = None) -> list[RefreshResult]:
        experiments = [self.store.get(identifier)] if identifier else self.store.active()
        updates: list[RefreshResult] = []
        for experiment in experiments:
            if not experiment.job_id or experiment.status in TERMINAL_STATES:
                continue
            status, exit_code, detail = self._slurm_status(experiment.job_id)
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
        return updates

    def cancel(self, identifier: str) -> ExperimentRecord:
        experiment = self.store.get(identifier)
        if not experiment.job_id:
            raise ValueError(f"experiment {experiment.id} has no submitted Slurm job")
        if experiment.status in TERMINAL_STATES:
            raise ValueError(f"experiment {experiment.id} is already terminal ({experiment.status})")
        self.runner(["scancel", experiment.job_id], check=True, text=True, capture_output=True)
        return self.store.update_status(
            experiment.id,
            "CANCELLED",
            detail=f"scancel requested for job {experiment.job_id}",
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
        current = self.runner(
            ["squeue", "--jobs", job_id, "--noheader", "--format=%T"],
            check=True,
            text=True,
            capture_output=True,
        )
        states = [line.strip() for line in current.stdout.splitlines() if line.strip()]
        if states:
            state = _normalize_state(states[0])
            return state, None, f"squeue: {states[0]}"

        history = self.runner(
            ["sacct", "--jobs", job_id, "--noheader", "--parsable2", "--allocations", "--format=State,ExitCode"],
            check=True,
            text=True,
            capture_output=True,
        )
        rows = [line.strip() for line in history.stdout.splitlines() if line.strip()]
        if not rows:
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
