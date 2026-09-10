"""Display allocation totals from the immutable submitted Slurm script."""

import hashlib
import re
from pathlib import Path


def resource_summary(experiment):
    try:
        raw = Path(experiment.script_path).read_bytes()
    except OSError:
        return {"available": False, "reason": "Submitted resource request is unavailable."}
    if hashlib.sha256(raw).hexdigest() != experiment.script_sha256:
        return {"available": False, "reason": "Submitted script has changed; resource totals cannot be verified."}
    return parse_resources(raw.decode("utf-8", errors="replace"))


def parse_resources(script):
    options = {}
    for line in script.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        match = re.match(r"#SBATCH\s+--([\w-]+)(?:=|\s+)([^#]+)", line)
        if match:
            options[match[1]] = match[2].strip()

    def integer(key):
        value = options.get(key, "")
        return int(value) if value.isdigit() else None

    nodes, tasks, cpu_per_task = integer("nodes"), integer("ntasks"), integer("cpus-per-task")
    cpus = tasks * cpu_per_task if tasks is not None and cpu_per_task is not None else None
    gpu_type = None
    gpus = 0
    for key, multiplier in (("gpus", 1), ("gpus-per-node", nodes), ("gpus-per-task", tasks), ("gres", nodes)):
        if key not in options:
            continue
        value = options[key]
        if key == "gres":
            entries = [item[4:] for item in value.split(",") if item.startswith("gpu:")]
            value = ",".join(entries)
        total = 0
        types = []
        for entry in value.split(","):
            match = re.fullmatch(r"(?:(\w[\w.-]*):)?(\d+)", entry)
            if not match:
                total = None
                break
            total += int(match[2])
            if match[1]:
                types.append(match[1].upper())
        gpus = total * multiplier if total is not None and multiplier is not None else None
        gpu_type = "/".join(dict.fromkeys(types)) or None
        break
    partition = options.get("partition")
    inferred = False
    if gpus and not gpu_type and partition:
        match = re.search(r"gpu(H200|H100|A100|A40|A30|V100|L40S|L40)(?:x|[-_]|$)", partition, re.IGNORECASE)
        if match:
            gpu_type = match[1].upper()
            inferred = True
    memory_mib = None
    memory_all = False
    memory_key = next((key for key in ("mem", "mem-per-cpu", "mem-per-gpu") if key in options), None)
    if memory_key:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT]?)", options[memory_key], re.IGNORECASE)
        if match:
            amount = float(match[1])
            memory_all = amount == 0
            multiplier = {"mem": nodes, "mem-per-cpu": cpus, "mem-per-gpu": gpus}[memory_key]
            if multiplier is not None and not memory_all:
                memory_mib = (
                    amount * {"K": 1 / 1024, "": 1, "M": 1, "G": 1024, "T": 1024**2}[match[2].upper()] * multiplier
                )
    return {
        "available": True,
        "nodes": nodes,
        "cpus": cpus,
        "gpus": gpus,
        "gpu_type": gpu_type,
        "gpu_type_from_partition": inferred,
        "memory_gib": memory_mib / 1024 if memory_mib is not None else None,
        "memory_all": memory_all,
        "partition": partition,
    }
