# Getting started

## Requirements

The submission host needs Python 3.11 or newer and the Slurm client commands
`sbatch`, `squeue`, `sacct`, and `scancel`. Compute nodes need `srun`,
`scontrol`, and Apptainer. Shared datasets, image caches, logs, and results
must be visible from the nodes that run the experiment.

## Install from source

```bash
git clone https://github.com/goncalorafaria/rexs.git
cd rexs
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

REXS uses [Python Fire](https://google.github.io/python-fire/) for its command
interface. Discover commands and generated option help with:

```bash
rexs --help
rexs submit --help
```

## Configure your site

```bash
cp examples/profile.yaml profile.yaml
```

At minimum, review the Slurm account and partition, shared run path, image
locations, and dataset mappings. See [Site configuration](configuration.md)
for every field.

## Validate and render

Validation parses and compiles without writing a script or calling Slurm:

```bash
rexs validate examples/datadev-sft.json --profile profile.yaml
```

Rendering writes the exact script that would be submitted:

```bash
rexs render examples/datadev-sft.json \
  --profile profile.yaml \
  --name sft-smoke \
  --output sft-smoke.sbatch

bash -n sft-smoke.sbatch
```

Use `--strict` when a warning should stop the command:

```bash
rexs validate experiment.yaml --profile profile.yaml --strict
```

## Template values

Only exact `${NAME}` placeholders are replaced. Shell expressions with
operators, such as `${BEAKER_REPLICA_RANK:-0}`, remain intact.

```bash
rexs render stack.yaml \
  --profile profile.yaml \
  --define='{"CLUSTER":"gpu","VLLM_REPLICAS":2}' \
  --output stack.sbatch
```

Assignments can also be passed individually when calling the Python API or as
a Fire-compatible list.

## Submit

```bash
rexs submit experiment.yaml \
  --profile profile.yaml \
  --name my-experiment \
  --strict
```

REXS saves the source specification and generated script metadata before it
calls `sbatch`. A failed submission remains visible in state history with its
error message.

## Dry-run many experiments

```bash
rexs dry_run ~/datadev/beaker_experiments
rexs dry_run ~/datadev/beaker_experiments --details
rexs dry_run ~/datadev/beaker_experiments \
  --strict \
  --output_dir=/tmp/rexs-rendered
```

Dry-run mode discovers Beaker v2 `.yaml`, `.yml`, and `.json` files, compiles
them, and feeds every script to `bash -n`. It never invokes a Slurm command.

