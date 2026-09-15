# Operations and dashboard

## Durable state

REXS stores experiments in SQLite at:

```text
~/.local/state/rexs/state.sqlite3
```

Override it with `--db=/path/to/state.sqlite3` or `REXS_STATE_DB`. WAL mode
allows the CLI and background server to access the database concurrently.

Each record includes the source snapshot, source and script SHA-256 hashes,
script location, Slurm job ID, warnings, task replicas, status, exit code, and
an append-only event history.

## CLI lifecycle

```bash
rexs experiments
rexs experiments --status=RUNNING
rexs show 123456
rexs status 123456
rexs refresh 123456
rexs logs 123456 --task=trainer --replica=0 --lines=200
rexs logs 123456 --task=trainer --follow
rexs cancel 123456
```

Identifiers may be either the REXS record ID or the Slurm job ID.

## State reconciliation

Active jobs are queried from `squeue`. Once absent there, REXS consults
`sacct` for the final allocation state and exit code. Slurm states are reduced
to stable values including `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`,
`CANCELLED`, `TIMEOUT`, `OUT_OF_MEMORY`, and `PREEMPTED`.

The controller never changes Slurm scheduling decisions. Cancellation is an
explicit `scancel` followed by a recorded state transition.

## Web dashboard

Run in the foreground:

```bash
rexs server --host=127.0.0.1 --port=8765
```

Or start a detached process:

```bash
rexs server --host=127.0.0.1 --port=8765 --daemon
rexs server_stop
```

The Beaker-inspired interface supports:

- experiment history and text search;
- running, queued, completed, failed, and canceled filters;
- submitted configuration inspection;
- per-task and per-replica logs;
- event history and Slurm metadata;
- guarded cancellation for active experiments.

The server polls Slurm in the background while it runs. Its own PID and logs
live beside the selected SQLite database.

## HTTP API

The dashboard uses a small JSON API:

| Method | Route | Action |
| --- | --- | --- |
| `GET` | `/api/experiments?status=RUNNING` | List and filter history |
| `GET` | `/api/experiments/<id>?lines=200` | Inspect one run and logs |
| `POST` | `/api/refresh` | Reconcile active jobs |
| `POST` | `/api/experiments/<id>/cancel` | Cancel an active job |

The server has no authentication layer. Bind to `127.0.0.1` unless it is
placed behind an authenticated reverse proxy.


## Experiments spanning multiple allocations

A Beaker v2 spec can define `rexs.allocations` with a name, task list, and site
profile for each group. Set `independent_replicas: true` to schedule replicas as
separate jobs, and `rexs.completion_task` to name the evaluator that owns cleanup.
Submit the spec once. All jobs appear under one experiment ID, with unified logs,
status, resource totals by GPU type, and cancellation. Replica selectors are
grouped by task; the legend matches experiment and allocation state colors.
Use Full screen to expand the log viewer.

See the [multi-allocation example](https://github.com/goncalorafaria/rexs/tree/main/examples/multi-allocation)
for a portable configuration. Existing single-allocation specs remain supported.

## Task failure policies for grouped experiments

Set `rexs.failure_policies` in the submitted spec. Each entry applies to every
replica of that logical task:

```yaml
rexs:
  failure_policies:
    policy:
      action: restart
      max_restarts: 3
    judge-model:
      action: restart
      max_restarts: 3
    redis:
      action: fail_experiment
  # allocations and completion_task as usual
```

`restart` submits a fresh job for the failed allocation, keeping the same REXS
experiment, task name and replica rank. Healthy independent replicas keep running.
The limit is per allocation, over its lifetime. Exhausting it fails the experiment
and cancels its remaining jobs. Failed, cancelled, preempted, timed-out and OOM
allocations qualify; a successful exit does not. `fail_experiment` cancels all
remaining allocations on failure. Omitted policies use `continue`, the existing
behavior: sibling jobs keep running, and the completion task determines the result.

This is a process restart, not automatic checkpoint recovery. Workloads must
restore their own durable checkpoints and register replacement endpoints using
stable discovery. Restarting an allocation referenced by `${jobs.NAME}` is
rejected because the replacement gets a new Slurm ID. All tasks packed into an
allocation must have the same policy; a packed allocation restarts as a whole.
Use independent replicas for independent recovery. Completion tasks and jobs with
their own dependency cleanup cannot be restarted. Put scheduler options in
profiles; submission-time sbatch overrides are rejected for restartable specs.

Keep the REXS server running (or invoke refresh regularly): it drives recovery and
fail-fast cleanup. A finished completion task takes precedence over service
recovery, and cancelling the experiment disables recovery. Cancellation and
refresh are serialized across REXS processes. Previous job IDs and failure states
are saved under each allocation's `attempts`; their files remain under the original
job run directory. The dashboard shows policies, retries and previous job IDs.
The evaluator's cleanup trap reads `owned-jobs.txt` beside its saved script at
exit, including replacement jobs; keep the artifact directory on shared storage.
The controller also cancels replacement jobs when completion is observed.

If the controller is interrupted between submitting a replacement and saving its
job ID, recovery pauses in `RESTARTING` instead of risking duplicate submission.
Inspect the scheduler and reconcile that allocation before continuing. These
policies are read from the submitted snapshot; editing the source YAML does not
change an already running experiment.

### Keeping recovery independent of SSH sessions

The recovery controller must keep running between refreshes. `rexs server --daemon`
detaches from the launching terminal, but a supervised systemd user service with
lingering enabled also survives login-session cleanup and restarts after crashes.
Run the foreground server (`--daemon=False`) in that service, set `Restart=always`
and `RestartSec=5`, enable it for `default.target`, and enable user lingering with
`loginctl enable-linger "$USER"`. The service needs the intended Python environment,
Slurm commands on PATH, and explicit persistent database/artifact paths.

On the current Klone installation, `rexs.service` serves port 25236 using the
existing state database. Its unit is at
`/gscratch/ark/graf/.config/systemd/user/rexs.service`, linked into the user
manager's configuration directory. Manage this installation with
`systemctl --user restart rexs.service` or `systemctl --user stop rexs.service`,
rather than the standalone daemon commands. Check `ActiveState`, `MainPID`, and
`ControlGroup` with `systemctl --user show rexs.service`; verify `Linger=yes` using
`loginctl show-user "$USER" -p Linger`. Server logs remain private because startup
output includes the authenticated dashboard URL.

Closing the SSH tunnel disconnects browser access only. The controller and Slurm
jobs continue; reconnect the tunnel to view progress. A controller-host outage
still pauses controller-driven recovery until that host/service is available.
Slurm jobs and their own cleanup traps run independently on their assigned nodes.

## Optional SQLite alerts

Alerts are a passive, durable feed for people or agents. They do not launch chats,
invoke hooks, send messages, pause a job, or require acknowledgment. Every alert
flag defaults to false; existing submitted experiments keep their settings.

Select the sources and outcomes in the experiment YAML:

```yaml
rexs:
  alerts:
    tasks:
      evaluation:
        completed: true
        failed: true
      policy:
        failed: true
        failure_reasons: [PREEMPTED, OUT_OF_MEMORY, NODE_FAIL]
    # Optional experiment-level outcome; omit to avoid a second completion alert.
    # experiment:
    #   completed: true
    #   failed: true
  # Existing allocations/completion_task/failure_policies can also be specified.
```

With only `rexs.alerts`, all tasks are compiled into one allocation using the
provided profile. With explicit allocations, task settings apply across their
replicas. Failure reasons are Slurm/REXS status codes: `FAILED`, `TIMEOUT`,
`NODE_FAIL`, `OUT_OF_MEMORY`, `PREEMPTED`, and `SUBMISSION_FAILED`. Omitting
`failure_reasons` selects all these failures; an empty list selects none. Queued,
running, unknown, cancelled and routine cleanup states do not generate alerts.
A selected replica failure can generate an alert even if its restart policy
subsequently recovers it. These options are independent of recovery policies.

The `alerts` table in the REXS state SQLite database contains ordered integer IDs,
creation time, experiment/allocation/task/job identifiers, `kind` (`completed` or
`failed`), `reason`, and versioned `context_json`. Context includes replica ranks,
attempt number, exit code, run root and dashboard path. Raw logs, environment
variables and credentials are not copied into alerts. For packed tasks, the status
is the allocation's observed outcome; it does not prove which child caused a
failure. Each selected task gets one alert covering its ranks in that allocation.

Writes are transactional with observed state updates and deduplicated by subject,
job attempt and outcome kind. Repeated refreshes, concurrent pollers and controller
restarts do not generate copies. Alerts are recorded when the controller observes
the outcome, not necessarily at the exact time Slurm finishes the job. No historical
backfill happens when this feature is installed.

Read without consuming:

```bash
rexs alerts --after=0 --limit=100
rexs alerts --experiment=EXPERIMENT_ID --after=0
```

Authenticated HTTP: `GET /api/alerts?after=0&limit=100`, optionally with
`experiment=EXPERIMENT_ID`. The response contains `alerts` and `next_after`; an empty
page preserves the input cursor. Each external agent can retain its own last
processed ID, page forward and decide whether to dispatch follow-up work. Reads
never change alert state; ignoring an alert has no effect on experiment execution.
Persist a consumer's cursor only after its own work is safely recorded—REXS does
not promise exactly-once external chat dispatch. For direct SQL readers:

```sql
SELECT id, experiment_id, task_name, job_id, kind, reason, context_json
FROM alerts WHERE id > :last_seen_id ORDER BY id LIMIT 100;
```

### Extend a running unified experiment

Use `rexs extend EXPERIMENT_ID --task model --replicas 2 --strict` to add two
independently scheduled replicas to an existing task. `replicas` is the number
added, not the desired total. The experiment retains its ID and dashboard row;
new jobs receive unique allocation names and task ranks, and appear in its logs,
status, resource totals, and cancellation scope. Existing replicas are untouched;
their already-exported replica-count environment variables remain unchanged.

The selected task must use `independent_replicas: true`. Completion tasks and
terminal experiments cannot be extended. Extensions resolve job references
against the original experiment and use the task's configured profile. All new
scripts validate before submission. Failed submissions roll back only the new
jobs and retain events for diagnosis; cleanup failures remain visible.

New job IDs are appended to the parent's `owned-jobs.txt`, read by current
completion traps. Experiments submitted before dynamic cleanup was introduced
rely on controller refresh/cancellation for extension cleanup; keep the controller
running for those older deployments. Applications should also bind serving
workers to the evaluator lifecycle. A completion check after submission catches
an evaluator exiting while the extension is being added.

### Alternative GPU hardware for one task

Use a profile with `gpu_types: [l40, l40s, a40]` instead of `gpu_type: l40`
and keep one task with `replicas: 3`. Its allocation group can set
`independent_replicas: true` so each replica schedules separately. REXS emits
an untyped GPU count and `--constraint=[l40|l40s|a40]`; Slurm chooses an eligible
node when capacity, priority, and other requirements allow it. The list is an
allowlist, not a preference order or an estimate of queue wait time. No duplicate
speculative jobs are submitted, and replicas may land on different GPU types.
Matching OR keeps a multi-node allocation on one accepted hardware type.

This requires node features named for the GPU types and homogeneous GPU hardware
within each node (as on Klone). Features constrain nodes, not individual GRES on
mixed-GPU nodes. Use a typed `gpu_type` profile on sites without that mapping.
Choose an account, partition and QOS eligible for every listed type; the list does
not grant access to other partitions or override accounting policy. Do not combine
`gpu_types` with `gpu_type` or raw sbatch GPU/constraint overrides. Existing typed
profiles remain supported. Updating a profile does not alter already submitted jobs.
