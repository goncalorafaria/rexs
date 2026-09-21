# Execution backends and the REXS intermediate format

REXS accepts existing Beaker v2 configurations and native `version: rexs/v1`
configurations. Both pass through `rexs.ir.Experiment`, which preserves task
intent, unresolved image/dataset references, and native metadata. Backend
planning resolves those references with a profile and produces an sbatch script
or a native Beaker experiment. The Slurm compiler and Beaker adapter are separate
modules; importing or running Slurm does not require the Beaker SDK.

## Run the same workload on either backend

Edit the example profiles for your site and account, then validate or render:

```bash
rexs normalize examples/backends/experiment.yaml
rexs validate examples/backends/experiment.yaml --profile examples/backends/slurm.yaml --strict
rexs render examples/backends/experiment.yaml --profile examples/backends/slurm.yaml

pip install -e '.[beaker]'
rexs validate examples/backends/experiment.yaml --profile examples/backends/beaker.yaml --strict
rexs render examples/backends/experiment.yaml --profile examples/backends/beaker.yaml
```

Validation, rendering, normalization, and `dry_run` are offline. Beaker planning
uses the optional SDK's schema parser but never constructs an authenticated
client. Actual submission uses your existing Beaker SDK configuration/token:

```bash
rexs submit examples/backends/experiment.yaml --profile examples/backends/beaker.yaml
rexs status REXS_ID
rexs logs REXS_ID --task hello
rexs cancel REXS_ID
```

The profile is needed at submission, not for later lifecycle commands. SQLite
records the backend and remote experiment ID. The controller routes status,
logs, and cancellation accordingly; Beaker IDs never reach Slurm commands.
Old database records migrate with `backend=slurm`. Results remain in the
backend's native storage: shared filesystem for Slurm, Beaker results for Beaker.

## Profiles

Existing flat Slurm profiles remain valid. Omitting `backend` selects Slurm.
New profiles can put Slurm policy under `slurm`, Apptainer settings under
`runtime`, and common resource mappings under `images` and `datasets`.
Do not mix flat and nested settings in one profile.

Beaker profiles accept `backend: beaker`, a `beaker` section, and image/dataset
mappings. The native section supports `workspace`, `budget`, `clusters`,
`priority`, and a `context` mapping. Explicit profile workspace/budget/cluster/
priority override input placement. Other context fields are defaults that
explicit task context overrides. Resource requests and lifecycle intent remain
in the experiment.

Native REXS uses the Beaker v2 task vocabulary, plus two portable conveniences:

```yaml
version: rexs/v1
tasks:
  - name: train
    image: trainer
    command: [python, /data/train.py]
    datasets:
      - mountPath: /data
        source: {ref: training-data}
```

On Slurm, map `trainer` to a SIF path or OCI URI and `training-data` to a host
directory. On Beaker, map images to a Beaker image name (string) or an explicit
`{beaker: ...}` / `{docker: ...}` mapping. Dataset strings mean Beaker dataset
IDs; explicit source mappings also support Weka, host paths, results, secrets,
and volumes supported by the installed SDK. Secret values are never resolved
by REXS; secret references stay in the submitted spec.

## Capability boundaries

Beaker native fields such as retry, propagation, context, and constraints are
retained. Unknown fields that the SDK would discard fail validation. REXS
`readOnly` mount flags translate to native Beaker `mode`.

Beaker currently rejects `rexs.allocations`, completion tasks, REXS failure
policies, and alerts instead of silently dropping their semantics. Dynamic
`extend` is Slurm-only. Beaker logs support snapshots and task/replica filtering;
`--follow` and `--paths` are explicitly unsupported. GPU metric capture, elapsed
runtime estimates, and dashboard resource totals remain Slurm-specific.

Rendered Beaker YAML contains the workload; workspace selection is supplied by
the profile when submitting. `rexs normalize` emits the unresolved IR and
preserves metadata; it does not apply a profile.

No new deployment is launched by installing this change. Backend tests use the
real SDK parser and protobuf types with a simulated client. A live Beaker smoke
run is still needed to verify account-specific images, mounts, and scheduling.
Transport failures during submission retain an `UNKNOWN` REXS record and never
automatically retry; inspect Beaker before submitting again.

SDK reference: https://beaker-py-docs.allen.ai/api/experiments.html
