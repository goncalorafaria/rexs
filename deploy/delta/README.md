# Delta LiteRegistry / Podman via Apptainer

Run the stack with Rex from `/u/galvesfaria/rexs`. The service workload is CPU-only.

## Images

`bash deploy/delta/build-images.sh` builds these SIFs under `deploy/delta/images`:

- `literegistry-redis.sif`
- `literegistry-gateway.sif`
- `literegistry-podman.sif`
- `literegistry-mirror.sif`
- `literegistry-warmup.sif`
- `literegistry-live-fire.sif`

They share `services.sif`, with role-specific runscripts. The base includes
LiteRegistry 1.0.47, the local Podman Beaker helper 0.2.16, Redis 7.0.15,
Distribution 3.1.1 and Debian Podman 4.3.1. These are native Apptainer adaptations,
not Docker-daemon builds of the upstream Dockerfiles (whose Podman image is
5.8.2). Python and Debian dependency inventories are embedded at
`/opt/python-packages.txt` and `/opt/debian-packages.txt`. Image checksums are in
`images/SHA256SUMS`; build output is in `logs/build.log`.

The build expects the LiteRegistry checkout at `/u/galvesfaria/literegistry`.
The checkout used was `4b5b5036318181d0a84f21b4db67c2bcb8973032`.
The pinned top-level packages and resulting SIF checksums are recorded; base
OCI tags and transitive dependencies are not locked for future rebuilds.

## Scheduling

The account audit found `bhfl-delta-gpu` and `noalloc`. Delta rejects CPU-only
allocations under the GPU account, and forbids submissions under `noalloc`.
`bhfl-delta-cpu` is not an authorized account for this user.

- `profile-cpu.yaml`: CPU partition, zero GPUs. Set an authorized CPU `account`
  before submitting; none was available during setup.
- `profile-a40-test.yaml`: `bhfl-delta-gpu`, `gpuA40x4`, one reserved GPU.
- `profile-h200-test.yaml`: `bhfl-delta-gpu`, `gpuH200x8-interactive`, one reserved GPU.

Both GPU profiles request 4 CPUs, 8 GiB, one node, and 10 minutes. The services
use no GPU, but Slurm reserves and charges the GPU to satisfy account policy.
REXS reports the task GPU count as zero; the reservation is a profile `sbatch`
directive. Both GPU partitions passed `sbatch --test-only`. H200 regular and
interactive partitions were authorized; interactive had the earlier estimate.

## Smoke deployment

```bash
source .venv/bin/activate
rexs validate deploy/delta/smoke.yaml --profile deploy/delta/profile-h200-test.yaml --strict
rexs render deploy/delta/smoke.yaml --profile deploy/delta/profile-h200-test.yaml \
  --strict --output deploy/delta/smoke-h200.sbatch
bash -n deploy/delta/smoke-h200.sbatch
# Reserves one H200 GPU; use only when this allocation choice is intended.
rexs submit deploy/delta/smoke.yaml --profile deploy/delta/profile-h200-test.yaml \
  --strict --name literegistry-delta-smoke
```

The bounded test starts Redis, gateway, mirror and Podman on one node using the
shared service contents of the Podman SIF. It checks Redis, gateway health,
Registry V2 routing, a real Ubuntu container handshake, file write/read across
two commands, and session close. It stops the services after testing. This is a
smoke deployment, not a persistent multi-node service installation. It does not
warm the full upstream image catalog or run the throughput/live-fire benchmark.

Inspect `runs/JOB_ID/results/stack-smoke/0/smoke-report.json` and the adjacent
service logs. The outer task log is `runs/JOB_ID/logs/stack-smoke.0.log`.
Services bind to loopback; no public endpoint is exposed by the test.

## Delta runtime details

`apptainer-delta` wraps `apptainer exec --fakeroot` and gives each execution a
private writable `/run`. This account has no subordinate UID/GID mappings.
Apptainer maps apparent root to the submitting user; it does not grant host
root privileges. See the [Apptainer fakeroot documentation](https://apptainer.org/user-docs/master/fakeroot.html).

`smoke.py` configures node-local VFS storage, single-UID ownership flattening,
file event logging, disabled inner cgroups, and reuse of the outer `/dev/pts`
mount. These are compatibility settings for this limited smoke test: arbitrary
multi-user images and inner per-container cgroup limits are not validated.
The Slurm allocation remains the resource boundary.

Podman and mirror caches live in a uniquely named `/tmp/rexs-podman-*` directory.
The report records its exact path. Scratch and `/tmp/rexs-runtime.*` directories
are left for inspection; service processes and the successful session are stopped.
Do not point these VFS stores at the NFS home filesystem.

## Validation completed

The local end-to-end smoke passed on `dt-login03`, including the real mirrored
Ubuntu pull, gateway handshake, stateful write/read and close. Its full result
is `logs/local-smoke-v2/smoke-report.json`. Earlier diagnostic logs are retained.
All six service SIFs built successfully; Rex strict compilation and shell/Python
checks passed. No Slurm job has been submitted yet: selecting a GPU reservation
for the CPU workload is still pending.

## Running shared stack

`stack.yaml` and `profile-a40-stack.yaml` deploy four Podman replicas, one
Redis, one gateway, and two mirrors **on one shared A40 node**. Rex's new
`tasks_per_node: 8` packs all replicas into one 16-core, 60-GiB allocation with
one reserved GPU. Each service has 2 dedicated cores; each Podman step has
8 GiB, and each other step has 4 GiB. Inner containers share their worker's
Slurm step memory limit; declared session budgets do not increase that limit.

Job `21899159` started on `gpub085` on September 9, 2026 at 00:34 Chicago time,
with a one-hour limit. All eight services became healthy and the gateway
session write/read/close test passed (`runs/21899159/gateway-test.json`).
The earlier stalled startup job `21899065` was cancelled and replaced.

```bash
# New shells activate the shared environment automatically.
source ~/.config/shell/main-env.sh
rexs experiments
rexs status 21899159
rexs show 21899159
rexs logs 21899159 --task=podman --replica=0 --follow
rexs logs 21899159 --task=gateway --replica=0
rexs logs 21899159 --task=mirror --replica=1
rexs logs 21899159 --task=redis --replica=0
# Stop the whole stack:
rexs cancel 21899159
# Start a new stack (creates a new job ID and job-scoped endpoints):
cd ~/rexs
rexs submit deploy/delta/stack.yaml --profile deploy/delta/profile-a40-stack.yaml \
  --strict --name literegistry-a40-shared
```

The gateway for job 21899159 is `http://gpub085:23181` (cluster network only).
Discover future job endpoints in `runs/JOB_ID/endpoints/*.json`; check `/health`
before using a published gateway URL. Endpoint files are job-scoped launch
records, not TTL-based health records. LiteRegistry maintains service health
and membership in Redis. `service.py` waits for upstream readiness before
acquiring the cooperative node port lock, avoiding startup deadlock when all
services share a node.

## Shared shell environment

The default Bash/Zsh environment is `~/.local/share/venvs/main` (Python 3.12).
It contains editable Rex and all three LiteRegistry subpackages, published
`literegistry[all]==1.0.47`, Pyserini, CPU PyTorch, Faiss CPU, and pip. Java 21
is installed at `~/.local/share/java/temurin-21`. Python dependency checks and
a real Lucene analyzer invocation passed. Shell setup lives in
`~/.config/shell/main-env.sh`; pre-change Bash/Zsh files are backed up there.
Explicitly activated environments are preserved. Already-open shells can run
`source ~/.local/share/venvs/main/bin/activate` followed by
`source ~/.config/shell/main-env.sh` to switch immediately.
