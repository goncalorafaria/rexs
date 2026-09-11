from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rexs.cli import _check_and_report
from rexs.compiler import compile_experiment
from rexs.config import SlurmProfile, load_experiment
from rexs.errors import RexsError, TranslationError

PROFILE = SlurmProfile.from_mapping(
    {
        "account": "research",
        "partition": "default-gpu",
        "qos": "normal",
        "time_limit": "12:00:00",
        "cpus_per_task": 2,
        "images": {
            "runtime": "/images/runtime.sif",
            "redis": "/images/redis.sif",
            "terminal": "/images/terminal.sif",
            "vllm": "docker://example/vllm:latest",
        },
        "datasets": {"weka:oe-adapt-default": "/weka"},
    }
)


def test_datadev_single_task_translation_is_shell_valid(tmp_path: Path) -> None:
    spec = {
        "version": "v2",
        "workspace": "ai2/datadev",
        "budget": "ai2/oe-omai",
        "tasks": [
            {
                "name": "sft",
                "image": {"beaker": "runtime"},
                "command": ["bash", "-c", "set -euo pipefail\nexec sft @ /weka/run.toml"],
                "envVars": [
                    {"name": "HOME", "value": "/weka/gfaria"},
                    {"name": "WANDB_API_KEY", "secret": "gfaria_WANDB_API_KEY"},
                ],
                "datasets": [{"mountPath": "/weka", "source": {"weka": "oe-adapt-default"}}],
                "result": {"path": "/tmp/beaker-result"},
                "resources": {"gpuCount": 4, "cpuCount": 16, "memory": "128 GiB"},
                "context": {"priority": "high", "minRuntime": "0h", "autoResume": True},
                "constraints": {"cluster": ["ai2/holmes"]},
            }
        ],
    }

    result = compile_experiment(spec, profile=PROFILE, job_name="sft run")

    assert "#SBATCH --job-name=sft__run" in result.script
    assert "#SBATCH --nodes=1" in result.script
    assert "#SBATCH --gpus-per-node=4" in result.script
    assert "#SBATCH --partition=default-gpu" in result.script
    assert "#SBATCH --account=research" in result.script
    assert "#SBATCH --qos=normal" in result.script
    assert any("experiment.workspace" in warning for warning in result.warnings)
    assert any("experiment.budget" in warning for warning in result.warnings)
    assert any("constraints" in warning for warning in result.warnings)
    assert any("context" in warning for warning in result.warnings)
    assert 'export APPTAINERENV_WANDB_API_KEY="${gfaria_WANDB_API_KEY}"' in result.script
    assert "--bind /weka:/weka" in result.script
    assert '--bind "$REXS_RUN_DIR/results/sft/0:/tmp/beaker-result"' in result.script
    _assert_bash_valid(result.script, tmp_path)


def test_literegistry_replicas_share_one_allocation(tmp_path: Path) -> None:
    spec = {
        "version": "v2",
        "tasks": [
            {
                "name": "redis",
                "image": {"beaker": "redis"},
                "command": ["redis-server"],
                "hostNetworking": True,
                "resources": {"cpuCount": 2},
                "constraints": {"cluster": ["ai2/holmes"]},
            },
            {
                "name": "terminal",
                "replicas": 2,
                "leaderSelection": True,
                "image": {"beaker": "terminal"},
                "command": ["bash", "-lc", 'terminal --rank "${BEAKER_REPLICA_RANK:-0}"'],
                "constraints": {"cluster": ["ai2/holmes"]},
            },
            {
                "name": "vllm",
                "image": {"beaker": "vllm"},
                "command": ["vllm", "serve", "model"],
                "resources": {"gpuCount": 1, "memory": "64 GiB"},
                "constraints": {"cluster": ["ai2/holmes"]},
            },
        ],
    }
    result = compile_experiment(spec, profile=PROFILE, job_name="registry")

    assert result.nodes == 4
    assert "#SBATCH --nodes=4" in result.script
    assert "#SBATCH --ntasks=4" in result.script
    assert "#SBATCH --gpus-per-node=1" in result.script
    assert "APPTAINERENV_BEAKER_REPLICA_RANK=1" in result.script
    assert "${BEAKER_REPLICA_RANK:-0}" in result.script
    assert 'APPTAINERENV_BEAKER_NODE_HOSTNAME="$node"' in result.script
    assert 'APPTAINERENV_BEAKER_JOB_ID="${SLURM_JOB_ID:-manual}"' in result.script
    assert result.script.count('REXS_PIDS+=("$!")') == 4
    _assert_bash_valid(result.script, tmp_path)


def test_unmapped_beaker_resources_use_predictable_paths() -> None:
    spec = {
        "version": "v2",
        "unknownTopLevel": True,
        "tasks": [
            {
                "name": "job",
                "image": {"beaker": "owner/image"},
                "command": ["true"],
                "datasets": [{"mountPath": "/data", "source": {"beaker": "owner/data"}}],
            }
        ],
    }
    result = compile_experiment(spec, profile=PROFILE)
    assert "/weka/gfaria/apptainer/images/owner__image.sif" in result.script
    assert "/weka/gfaria/datasets/beaker/owner__data:/data" in result.script
    assert any("unknownTopLevel" in warning for warning in result.warnings)
    assert any("unmapped Beaker image" in warning for warning in result.warnings)


def test_recognized_and_nested_unsupported_features_warn(capsys: pytest.CaptureFixture[str]) -> None:
    spec = {
        "version": "v2",
        "retry": {},
        "groups": [],
        "tasks": [
            {
                "name": "job",
                "image": {"docker": "busybox", "pullPolicy": "always"},
                "command": ["true"],
                "synchronizedStartTimeout": "5m",
                "propagatePreemption": False,
                "envVars": [{"name": "MODE", "value": "test", "description": "unsupported"}],
                "datasets": [
                    {
                        "mountPath": "/data",
                        "source": {"hostPath": "/tmp", "subPath": "nested"},
                        "readOnly": True,
                        "mountOptions": ["nodev"],
                    }
                ],
            }
        ],
    }

    result = compile_experiment(spec, profile=PROFILE)

    expected = (
        "experiment.retry",
        "experiment.groups",
        "synchronizedStartTimeout",
        "propagatePreemption",
        "pullPolicy",
        "description",
        "mountOptions",
        "subPath",
    )
    for feature in expected:
        assert any(feature in warning for warning in result.warnings), feature

    _check_and_report(result, strict=False)
    assert "warning:" in capsys.readouterr().err
    with pytest.raises(RexsError, match="strict translation refused warnings") as exc_info:
        _check_and_report(result, strict=True)
    assert "pullPolicy" in str(exc_info.value)


def test_template_values_are_exact_and_shell_defaults_survive(tmp_path: Path) -> None:
    source = tmp_path / "experiment.yaml"
    source.write_text(
        """\
version: v2
tasks:
  - name: service
    replicas: ${REPLICAS}
    image: {docker: busybox:latest}
    command: [bash, -lc, 'echo ${BEAKER_REPLICA_RANK:-0}']
""",
        encoding="utf-8",
    )
    spec = load_experiment(source, {"REPLICAS": "3"})
    result = compile_experiment(spec, profile=PROFILE)
    assert result.nodes == 3
    assert "${BEAKER_REPLICA_RANK:-0}" in result.script


def test_unresolved_integer_template_has_actionable_error() -> None:
    spec = {
        "version": "v2",
        "tasks": [
            {
                "name": "x",
                "replicas": "${COUNT}",
                "image": {"docker": "busybox"},
                "command": ["true"],
            }
        ],
    }
    with pytest.raises(TranslationError, match="--set KEY=VALUE"):
        compile_experiment(spec, profile=PROFILE)


def _assert_bash_valid(script: str, tmp_path: Path) -> None:
    target = tmp_path / "job.sbatch"
    target.write_text(script, encoding="utf-8")
    subprocess.run(["bash", "-n", str(target)], check=True)


def test_cpu_packing_places_replicas_and_sums_memory(tmp_path: Path) -> None:
    profile = SlurmProfile.from_mapping(
        {
            "tasks_per_node": 2,
            "cpus_per_task": 2,
            "memory": "4G",
            "images": {"runtime": "/images/runtime.sif"},
            "sbatch": {"gpus-per-node": 1},
        }
    )
    spec = {
        "version": "v2",
        "tasks": [
            {"name": "redis", "image": {"beaker": "runtime"}, "command": ["true"], "resources": {"memory": "2G"}},
            {
                "name": "podman",
                "replicas": 3,
                "image": {"beaker": "runtime"},
                "command": ["true"],
                "resources": {"memory": "6G"},
            },
        ],
    }
    result = compile_experiment(spec, profile=profile)
    assert (result.nodes, result.tasks) == (2, 4)
    assert "#SBATCH --ntasks=4\n" in result.script
    assert "#SBATCH --ntasks-per-node=2\n" in result.script
    assert "#SBATCH --mem=12G\n" in result.script
    assert result.script.count('local node="${REXS_NODES[0]}"') == 2
    assert result.script.count('local node="${REXS_NODES[1]}"') == 2
    assert result.script.count('BEAKER_LEADER_REPLICA_HOSTNAME="${REXS_NODES[0]}"') == 4
    assert result.script.count("--gres=none") == 4
    assert result.script.count("--mem=6G") == 3
    _assert_bash_valid(result.script, tmp_path)


@pytest.mark.parametrize("resources", [{}, {"gpuCount": 1, "memory": "2G"}])
def test_packing_rejects_ambiguous_step_resources(resources) -> None:
    profile = SlurmProfile.from_mapping({"tasks_per_node": 2})
    spec = {
        "version": "v2",
        "tasks": [
            {"image": {"docker": "ubuntu"}, "command": ["true"], "resources": resources},
        ],
    }
    with pytest.raises(TranslationError):
        compile_experiment(spec, profile=profile)


def test_bundled_dataset_does_not_mask_image_contents():
    profile = SlurmProfile(images={"runtime": "/images/runtime.sif"}, bundled_datasets=("weka:fixtures",))
    spec = {
        "version": "v2",
        "tasks": [
            {
                "name": "sft",
                "image": {"beaker": "runtime"},
                "command": ["true"],
                "datasets": [{"mountPath": "/data", "source": {"weka": "fixtures"}}],
            }
        ],
    }
    result = compile_experiment(spec, profile=profile)
    assert "--bind" not in result.script
    assert not result.warnings


@pytest.mark.parametrize("shared_pool", [None, 96])
def test_joint_gpu_cpu_replicas_share_one_allocation(tmp_path, shared_pool):
    profile = SlurmProfile(
        tasks_per_node=9, completion_task="rl", shared_cpus_per_node=shared_pool, images={"runtime": "/image.sif"}
    )
    tasks = [
        {
            "name": "podman",
            "replicas": 4,
            "image": {"beaker": "runtime"},
            "command": ["sleep", "60"],
            "resources": {"cpuCount": 2, "memory": "8G"},
        },
        {
            "name": "mirror",
            "replicas": 2,
            "image": {"beaker": "runtime"},
            "command": ["sleep", "60"],
            "resources": {"cpuCount": 2, "memory": "4G"},
        },
        {
            "name": "redis",
            "image": {"beaker": "runtime"},
            "command": ["sleep", "60"],
            "resources": {"cpuCount": 2, "memory": "4G"},
        },
        {
            "name": "gateway",
            "image": {"beaker": "runtime"},
            "command": ["sleep", "60"],
            "resources": {"cpuCount": 2, "memory": "4G"},
        },
        {
            "name": "rl",
            "image": {"beaker": "runtime"},
            "command": ["true"],
            "resources": {"gpuCount": 8, "cpuCount": 64, "memory": "512G"},
        },
    ]
    result = compile_experiment({"version": "v2", "tasks": tasks}, profile=profile)
    assert result.nodes == 1 and result.tasks == 9 and not result.warnings
    assert f"#SBATCH --cpus-per-task={shared_pool or 80}" in result.script
    if shared_pool:
        assert result.script.count("--overlap") == 8
        assert result.script.count("--cpu-bind=none") == 9
        assert result.script.count("--cpus-per-task=96") == 9
        assert result.script.count("--exclusive") == 1
    else:
        assert result.script.count("--exclusive") == 9
        assert "--overlap" not in result.script
    assert "#SBATCH --gpus-per-node=8" in result.script
    assert "#SBATCH --mem=560G" in result.script
    assert result.script.count("--gres=none") == 8
    assert result.script.count("--gpus-per-task=8") == 1
    assert "REXS_NODES[1]" not in result.script
    subprocess.run(["bash", "-n"], input=result.script, text=True, check=True)


@pytest.mark.parametrize(
    "primary,service,expected",
    [
        ("sleep .1; exit 0", "sleep 10", 0),
        ("sleep .1; exit 7", "sleep 10", 7),
        ("sleep 10", "sleep .1; exit 0", 1),
    ],
)
def test_joint_lifecycle_ends_with_training_and_fails_on_service_exit(primary, service, expected):
    from rexs.compiler import _supervisor

    script = """set -euo pipefail
cleanup() { for pid in "${REXS_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done; for pid in "${REXS_PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done; }
REXS_RUN_DIR=/tmp
REXS_PIDS=()
"""
    script += f'( {primary} ) &\nREXS_COMPLETION_PID=$!\nREXS_PIDS+=("$!")\n'
    script += f'( {service} ) &\nREXS_PIDS+=("$!")\n'
    script += "\n".join(_supervisor("rl"))
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5, check=False)
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize("pool", [0, -1, True, "96"])
def test_shared_cpu_pool_rejects_invalid_size(pool):
    from rexs.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="positive integer"):
        SlurmProfile.from_mapping({"shared_cpus_per_node": pool, "completion_task": "rl"})


def test_shared_cpu_pool_requires_joint_lifecycle():
    from rexs.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="requires completion_task"):
        SlurmProfile.from_mapping({"shared_cpus_per_node": 96})
