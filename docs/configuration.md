# Site configuration

The profile separates cluster policy from experiment intent. Pass it to
`validate`, `render`, or `submit` with `--profile`.

```yaml
account: research
partition: gpu
qos: normal
time_limit: "24:00:00"
cpus_per_task: 8
memory: 64G

apptainer_binary: apptainer
image_cache: /shared/rexs/images
image_dir: /shared/apptainer/images
dataset_root: /shared/datasets
run_root: /shared/rexs/runs
secret_file: ~/.config/rexs/secrets.env

setup_commands:
  - module load apptainer

sbatch:
  constraint: a100
  signal: B:TERM@120

images:
  org/trainer: /shared/apptainer/images/trainer.sif
  docker:org/api: docker://ghcr.io/org/api:latest

datasets:
  weka:shared-data: /shared/data
  beaker:org/tokenized: /shared/datasets/tokenized
```

## Profile fields

| Field | Default | Purpose |
| --- | --- | --- |
| `account` | unset | Slurm account |
| `partition` | unset | Slurm partition |
| `qos` | unset | Slurm QOS |
| `time_limit` | `24:00:00` | Allocation wall time |
| `cpus_per_task` | `4` | Default CPU count when a task omits it |
| `memory` | unset | Default memory per node |
| `apptainer_binary` | `apptainer` | Executable or absolute path |
| `image_cache` | `.rexs/images` | Cache for OCI images converted to SIF |
| `image_dir` | `/weka/gfaria/apptainer/images` | Fallback directory for Beaker images |
| `dataset_root` | `/weka/gfaria/datasets` | Fallback root for Beaker datasets |
| `run_root` | `.rexs/runs` | Logs and results root |
| `secret_file` | unset | Shell environment file loaded inside the allocation |
| `setup_commands` | empty | Commands executed before runtime setup |
| `sbatch` | empty | Additional `#SBATCH` key/value directives |
| `images` | empty | Image name to OCI URI or SIF mapping |
| `datasets` | empty | Dataset source to host-path mapping |

Unknown profile keys are errors. This is deliberate: a typo in scheduling or
storage policy should not silently submit a different job.

## Image resolution

Image mappings accept the plain reference or a qualified `beaker:`/`docker:`
key. Values may be existing SIF paths or any URI accepted by Apptainer.

- `image.docker` defaults to `docker://<reference>`.
- Mapped Beaker images use the configured value.
- An unmapped Beaker image falls back to
  `<image_dir>/<sanitized-reference>.sif` and emits a warning.

OCI images are pulled once under `image_cache` by the generated job script.
Use a shared cache when replicas may land on different nodes.

## Dataset resolution

- `source.hostPath` binds the path directly.
- `source.weka` uses a `weka:<name>` mapping. An unmapped `/weka` mount binds
  `/weka` and warns.
- `source.beaker` uses a `beaker:<name>` mapping or falls back under
  `<dataset_root>/beaker/` and warns.

## Secrets

The optional `secret_file` is sourced by the generated script with shell
export enabled. A Beaker environment entry such as:

```yaml
envVars:
  - name: WANDB_API_KEY
    secret: gfaria_WANDB_API_KEY
```

reads `gfaria_WANDB_API_KEY` from that file and exposes it inside the container
as `WANDB_API_KEY`. Secret values never appear in the experiment snapshot,
generated script, or SQLite database.

## Runtime overrides

These environment variables override their corresponding profile values at
submission runtime:

- `REXS_RUN_ROOT`
- `REXS_IMAGE_CACHE`
- `REXS_APPTAINER`


### Pack CPU replicas onto shared nodes

Set `tasks_per_node` (default `1`) to place multiple CPU task replicas on each
allocated node. Replicas are packed in experiment order, including across task
boundaries. For eight services sharing a 16-core allocation:

```yaml
tasks_per_node: 8
cpus_per_task: 2
memory: 60G
```

Set each task's `resources.cpuCount` to `2` and give every task an explicit
`resources.memory`. Rex sums task memory per node and uses the larger of that
sum and the profile memory. Each task runs as an exclusive, exact Slurm step
with its own CPU/memory request. The allocation uses the largest CPU request
as its uniform CPUs-per-task size. Host-network ports must be unique per node.
Packing currently supports CPU-only tasks; profile-level GPU reservations can
still satisfy a site's account policy, and packed CPU steps use `--gres=none`
to avoid serializing on the reserved GPU.

## Intercepting Beaker experiment submissions

Existing tools can keep generating Beaker v2 specs and invoking
`beaker experiment create`. An opt-in shell shim can route that command to Rex:

```bash
mkdir -p ~/.local/share/rexs/beaker-shim
cat > ~/.local/share/rexs/beaker-shim/beaker <<'SH'
#!/usr/bin/env bash
exec python -m rexs.beaker "$@"
SH
chmod +x ~/.local/share/rexs/beaker-shim/beaker
export REXS_PROFILE=/absolute/path/to/profile.yaml
export PATH="$HOME/.local/share/rexs/beaker-shim:$PATH"
```

This adapter supports `experiment create SPEC --name NAME --workspace WORKSPACE`.
It submits through Rex and returns Rex's JSON receipt; it does not emulate other
Beaker API commands or Beaker receipt formats. Unsupported commands fail explicitly.
The workspace is accepted as metadata; the Rex profile selects the Slurm account,
partition, image mappings, and dataset mappings. Leave the shim off `PATH` to use
Beaker normally. `REXS_DRY_RUN=1` renders an sbatch file beside the spec without
submitting it.

For images containing their input datasets at the original mount paths, declare
qualified dataset references in `bundled_datasets`, for example:

```yaml
bundled_datasets:
  - weka:my-fixtures
```

Rex then omits those input mounts so they do not hide the image's bundled files.
Other input datasets and result mounts retain their usual behavior. The image
must contain the input files at exactly the paths expected by the experiment.
