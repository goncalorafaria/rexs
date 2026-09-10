import hashlib
from types import SimpleNamespace

from rexs.resources import parse_resources, resource_summary


def test_multinode_totals_and_partition_model():
    result = parse_resources("""#!/bin/bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=8
#SBATCH --gpus-per-node=4
#SBATCH --mem=64G
#SBATCH --partition=gpuH200x8-interactive
""")
    assert (result["gpus"], result["cpus"], result["memory_gib"], result["nodes"]) == (8, 16, 128, 2)
    assert result["gpu_type"] == "H200"
    assert result["gpu_type_from_partition"]


def test_packed_cpu_allocation_and_header_overrides():
    result = parse_resources("""#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --cpus-per-task=2
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --gpus-per-node=1
true
#SBATCH --nodes=99
""")
    assert result["cpus"] == 32
    assert result["memory_gib"] == 16
    assert result["gpus"] == 1
    assert result["nodes"] == 1


def test_typed_gres_and_per_cpu_memory():
    result = parse_resources("""#SBATCH --nodes=2
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=8
#SBATCH --gres=gpu:a100:2
#SBATCH --mem-per-cpu=2048M
""")
    assert result["gpus"] == 4
    assert result["gpu_type"] == "A100"
    assert result["memory_gib"] == 64
    assert not result["gpu_type_from_partition"]
    assert parse_resources("#SBATCH --mem=0")["memory_all"]


def test_missing_and_modified_scripts_are_not_reported_as_verified(tmp_path):
    path = tmp_path / "job.sbatch"
    raw = b"#SBATCH --nodes=1\n"
    experiment = SimpleNamespace(script_path=str(path), script_sha256=hashlib.sha256(raw).hexdigest())
    assert not resource_summary(experiment)["available"]
    path.write_bytes(raw)
    assert resource_summary(experiment)["nodes"] == 1
    path.write_bytes(b"#SBATCH --nodes=99\n")
    assert not resource_summary(experiment)["available"]
