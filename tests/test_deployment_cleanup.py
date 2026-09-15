from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from rexs.compiler import compile_experiment
from rexs.config import SlurmProfile
from rexs.errors import ConfigurationError


def make_script(tmp_path: Path, *, setup_failure: bool = False):
    binary = tmp_path / "bin"
    binary.mkdir()
    commands = {
        "apptainer": "#!/usr/bin/env bash\nexit 0\n",
        "scontrol": '#!/usr/bin/env bash\nprintf "node1\\n"\n',
        "srun": '#!/usr/bin/env bash\n: > "$STARTED"\nif [[ ${BLOCK_TASK:-0} == 1 ]]; then exec sleep 30; fi\nexit "${TASK_STATUS:-0}"\n',
        "scancel": '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CANCEL_LOG"\nexit "${CANCEL_STATUS:-0}"\n',
    }
    for name, content in commands.items():
        path = binary / name
        path.write_text(content)
        path.chmod(0o755)
    image = tmp_path / "runtime.sif"
    image.touch()
    profile = SlurmProfile.from_mapping(
        {
            "images": {"runtime": str(image)},
            "image_cache": str(tmp_path / "cache"),
            "run_root": str(tmp_path / "runs"),
            "cleanup_job_ids": ["101", "202", "999"],
            "setup_commands": ["exit 9"] if setup_failure else [],
        }
    )
    spec = {
        "version": "v2",
        "tasks": [{"name": "evaluation", "image": {"beaker": "runtime"}, "command": ["python", "-m", "evaluator"]}],
    }
    script = tmp_path / "evaluation.sh"
    script.write_text(compile_experiment(spec, profile=profile).script)
    env = dict(
        os.environ,
        PATH=str(binary) + ":" + os.environ["PATH"],
        SLURM_JOB_ID="999",
        SLURM_JOB_NODELIST="node1",
        CANCEL_LOG=str(tmp_path / "cancel.log"),
        STARTED=str(tmp_path / "started"),
    )
    return script, env


def assert_scoped_cleanup(tmp_path: Path):
    lines = (tmp_path / "cancel.log").read_text().splitlines()
    assert len(lines) == 2
    assert [line.split()[-1] for line in lines] == ["101", "202"]
    assert all(line.startswith("--user=") for line in lines)


@pytest.mark.parametrize("task_status,cancel_status", [(0, 0), (7, 0), (7, 1)])
def test_cleanup_on_success_and_failure_preserves_exit(tmp_path, task_status, cancel_status):
    script, env = make_script(tmp_path)
    env.update(TASK_STATUS=str(task_status), CANCEL_STATUS=str(cancel_status))
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == task_status, result.stderr
    assert_scoped_cleanup(tmp_path)
    if cancel_status:
        assert "could not cancel deployment job" in result.stderr


def test_cleanup_even_when_setup_fails(tmp_path):
    script, env = make_script(tmp_path, setup_failure=True)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 9
    assert_scoped_cleanup(tmp_path)
    assert not (tmp_path / "started").exists()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_cleanup_on_signal(tmp_path, signum):
    script, env = make_script(tmp_path)
    env["BLOCK_TASK"] = "1"
    process = subprocess.Popen(
        ["bash", str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "started").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        process.send_signal(signum)
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 128 + signum, stderr
        assert_scoped_cleanup(tmp_path)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize("value", ["101", [101], ["--user=other"], ["0"], ["101; touch /tmp/no"]])
def test_cleanup_rejects_non_job_targets(value):
    with pytest.raises(ConfigurationError, match="cleanup_job_ids"):
        SlurmProfile.from_mapping({"cleanup_job_ids": value})


def test_cleanup_reads_replacement_jobs_at_exit(tmp_path):
    script, env = make_script(tmp_path)
    owned = tmp_path / "owned-jobs.txt"
    script.write_text(script.read_text().replace("REXS_CLEANUP_JOB_FILE=''", f'REXS_CLEANUP_JOB_FILE="{owned}"'))
    # Added after compilation: replacements must also be covered by the trap.
    owned.write_text("303\n--user=other\n999\n")
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert [line.split()[-1] for line in (tmp_path / "cancel.log").read_text().splitlines()] == ["101", "202", "303"]
