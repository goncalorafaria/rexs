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

