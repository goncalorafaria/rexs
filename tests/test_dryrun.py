from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rexs.cli import Rexs
from rexs.dryrun import dry_run_experiments
from rexs.errors import RexsError


def test_dry_run_compiles_only_v2_experiments_without_slurm(monkeypatch, tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(
        json.dumps(
            {
                "version": "v2",
                "tasks": [
                    {
                        "name": "worker",
                        "image": {"docker": "busybox:latest"},
                        "command": ["echo", "hello"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "not-an-experiment.yaml").write_text("name: settings\n", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("rexs.dryrun.subprocess.run", runner)
    report = dry_run_experiments([tmp_path])

    assert report["experiments"] == 1
    assert report["passed"] == 1
    assert report["failed"] == 0
    assert report["skipped_non_experiments"] == 1
    assert report["slurm_commands_invoked"] is False
    assert commands == [["bash", "-n"]]


def test_dry_run_reports_translation_failures_instead_of_stopping(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        """\
version: v2
tasks:
  - name: missing-command
    image: {docker: busybox:latest}
""",
        encoding="utf-8",
    )

    report = dry_run_experiments([invalid])

    assert report["experiments"] == 1
    assert report["passed"] == 0
    assert report["failed"] == 1
    assert "needs command or arguments" in report["failures"][0]["error"]


def test_strict_dry_run_reports_the_unsupported_feature(tmp_path: Path) -> None:
    experiment = tmp_path / "unsupported.yaml"
    experiment.write_text(
        """\
version: v2
workspace: ai2/example
tasks:
  - name: worker
    image: {docker: busybox:latest}
    command: ["true"]
""",
        encoding="utf-8",
    )

    normal_report = dry_run_experiments([experiment])
    assert normal_report["warning_categories"]["unsupported Beaker features"] == 1

    report = dry_run_experiments([experiment], strict=True)

    assert report["passed"] == 0
    assert report["failed"] == 1
    assert "experiment.workspace" in report["failures"][0]["error"]

    with pytest.raises(RexsError, match="strict dry run failed") as exc_info:
        Rexs().dry_run(str(experiment), strict=True)
    assert "experiment.workspace" in str(exc_info.value)


DATADEV_CORPUS = Path.home() / "datadev" / "beaker_experiments"


@pytest.mark.skipif(not DATADEV_CORPUS.is_dir(), reason="local datadev corpus is unavailable")
def test_local_datadev_beaker_corpus_is_shell_valid() -> None:
    report = dry_run_experiments([DATADEV_CORPUS])

    assert report["experiments"] >= 250
    assert report["failed"] == 0, report["failures"]
    assert report["passed"] == report["experiments"]
    assert report["slurm_commands_invoked"] is False
