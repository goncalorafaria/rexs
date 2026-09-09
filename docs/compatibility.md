# Beaker compatibility

REXS implements the experiment features used by datadev and LiteRegistry. It
does not aim for full Beaker API or scheduler compatibility.

## Supported

| Beaker field | REXS behavior |
| --- | --- |
| `version: v2` | Required |
| `tasks[].name` | Preserved and sanitized for paths |
| `image.beaker` | Resolved through image mappings or fallback SIF path |
| `image.docker` | Resolved through mappings or `docker://` |
| `command`, `arguments` | Combined as the container command |
| `envVars[].value` | Passed through Apptainer environment |
| `envVars[].secret` | Loaded by secret name inside the allocation |
| `replicas` | One exclusive `srun` step per replica |
| `leaderSelection` | Compatibility leader environment variables |
| `datasets[].source.weka` | Host bind through mappings or `/weka` fallback |
| `datasets[].source.hostPath` | Direct host bind |
| `datasets[].source.beaker` | Host bind through mappings or fallback path |
| `datasets[].readOnly` | Read-only bind option |
| `result.path` | Per-replica persistent result bind |
| `resources.gpuCount` | Homogeneous Slurm GPU request |
| `resources.cpuCount` | Homogeneous Slurm CPU request |
| `resources.memory` | Largest parseable memory request |
| `hostNetworking` | Host networking supplied by Apptainer |

## Warning-only or unsupported

| Beaker field | Behavior |
| --- | --- |
| `workspace`, `budget` | Ignored; Slurm account comes from the profile |
| `context.priority` | Ignored; Slurm QOS/partition come from the profile |
| `context.minRuntime`, `autoResume` | Ignored |
| `constraints.cluster` | Ignored; partition comes from the profile |
| `retry` | Ignored; use site Slurm requeue policy |
| `groups` | Ignored |
| `synchronizedStartTimeout` | Ignored |
| `propagatePreemption` | Ignored; preemption applies to the allocation |
| `propagateFailure: false` | Ignored; REXS is fail-fast |
| `resources.sharedMemory` | Advisory warning; Apptainer uses host IPC |
| unknown fields | Reported with their precise location |

Without `--strict`, these distinctions appear as warnings in CLI output,
generated script comments, SQLite state, and dry-run reports. With `--strict`,
any warning aborts validation, rendering, or submission.

## Not provided

REXS does not provide Beaker workspaces, budgets, image storage, datasets,
result uploads, retry accounting, scheduling groups, clusters, or an API
compatible Beaker server. Existing Beaker experiment files are compiler input;
Slurm and the shared filesystem provide execution and persistence.

## Site validation

A successful offline dry run proves parsing, translation, and shell syntax. It
cannot prove that a particular cluster has the mapped images, mounts, GPU
shape, network behavior, user namespaces, or resource capacity. Validate those
properties with a small real Slurm submission before scaling a deployment.

