# Translation model

## Allocation sizing

One experiment becomes one homogeneous Slurm allocation:

- node count is the sum of every task's replicas;
- GPUs per node are the largest `gpuCount` requested by any replica;
- CPUs per task are the largest `cpuCount`, falling back to the profile;
- memory is the largest parseable task memory request, falling back to the
  profile.

This model keeps node assignment, host networking, service discovery, and
job-wide cleanup deterministic. A mixed CPU/GPU stack can over-provision some
nodes; REXS reports that as a warning, and `--strict` rejects it.

## Replica execution

The generated script asks Slurm for the full allocation, expands its node
list, and starts one exclusive `srun` step per replica. Each process runs:

```text
srun → apptainer exec → experiment command
```

The container receives compatibility variables used by existing Beaker-aware
applications:

| Variable | Meaning |
| --- | --- |
| `BEAKER_REPLICA_RANK` | Zero-based rank within a task |
| `BEAKER_REPLICA_COUNT` | Replica count for that task |
| `BEAKER_LEADER_REPLICA` | Leader marker when requested |
| `BEAKER_NODE_HOSTNAME` | Assigned Slurm node hostname |
| `BEAKER_JOB_ID` | Slurm allocation ID |

REXS-specific task and run metadata are also provided to the container.

## Networking and coordination

Apptainer shares the host network by default. This matches the datadev and
LiteRegistry usage of host-networked services. Existing coordination through
shared Weka paths and replica-derived ports therefore continues to work.

## Logs and results

Each replica writes to a stable log file:

```text
<run-root>/<job-id>/logs/<task>.<rank>.log
```

If a task declares `result.path`, REXS creates a per-replica host directory and
binds it at that container path:

```text
<run-root>/<job-id>/results/<task>/<rank>/
```

## Failure behavior

The batch script supervises all `srun` children. The first failed replica
causes sibling steps to be terminated, and the allocation exits non-zero. This
is intentionally stronger than Beaker's `propagateFailure: false` behavior and
is reported when that distinction appears in the source.

## Inspectability

`rexs render` is the best way to understand a translation. The output is a
normal shell script: no hidden daemon or remote compiler participates in job
execution.

