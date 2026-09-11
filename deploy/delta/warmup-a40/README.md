# A40 mirror warmup stack

```bash
rexs submit /u/galvesfaria/rexs/deploy/delta/warmup-a40/stack.yaml --profile /u/galvesfaria/rexs/deploy/delta/warmup-a40/profile.yaml --name literegistry-mirror-warmup --strict
```

One A40 allocation: 64 shared CPUs, 68 GiB, one GPU reserved by the partition
profile, one hour. Redis, gateway, four Docker mirrors, one Podman worker and a
warmup task share the CPU pool without exclusive CPU binding.

Warmup reads train/eval JSONL dataset paths from the mounted PrimeBeaker
`deploy/podman-colocated/rl.toml`. It extracts task image fields using the
environment's lookup precedence, qualifies and deduplicates image names, then
uses LiteRegistry docker_mirror_warmup to fetch their linux/amd64 manifests and
blobs. Missing images or unsupported registries fail rather than silently skip.
The current datasets have two train rows and one eval row and resolve to one
unique Python image. The old manually maintained images.txt is unused.

Each mirror uses its own node-local temporary directory:
`/tmp/rexs-JOB-mirror-RANK-.../mirror`. It is not a persistent home-directory
cache. The exact directory is recorded in `../runs/JOB/endpoints/mirror.0.json`
and in the warmup ready report. Existing historical home caches are not deleted.

Warmup verifies a Podman create/execute/close lifecycle with the first discovered
image, then stays alive to keep services available. Results under
`../runs/JOB/results/warmup/0` include images.txt, datasets.json and ready.json.
Logs are under `../runs/JOB/logs`. Warmup failure terminates the service stack.

H200 training must point to this running warmed mirror to benefit from it;
its separate fresh mirrors do not share this temporary cache.

Every mirror is warmed directly in a separate LiteRegistry process so blob
deduplication cannot skip other mirrors. Four mirrors bring the total to eight
replicas, sharing 64 CPUs. Each has its own temporary cache.
