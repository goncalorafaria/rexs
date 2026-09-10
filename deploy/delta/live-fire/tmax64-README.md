# Recorded Tmax replay — 64 workloads

Source: LiteRegistry commit 8450dd3, bundled assets/tmax_deployment_workloads.jsonl (4,105 workloads). Selection: shuffle_seed=0, first 64 after shuffle; 64 unique images, 1,246 recorded commands. Selected rows and manifest checksum are saved alongside this file.

Run in the main shell environment:

```bash
literegistry-podman-live-fire http://gpub006:22721 \
  --total=64 --concurrency=4 --expected_podman=4 \
  --wait_timeout=120 --command_timeout=60 --retries=1 --log_every=4 \
  --checkpoint_file=/u/galvesfaria/rexs/deploy/delta/live-fire/tmax64-21900136.checkpoint
```

The gateway belongs to Slurm job 21900136 and expires with that allocation. The checkpoint skips completed traces on restart; omit it or use a new filename for a fresh replay.

Watch the current run:

```bash
tail -f /u/galvesfaria/rexs/deploy/delta/live-fire/tmax64-21900136.log
squeue -j 21900136
```

This is recorded-command replay, without model inference or final-state grading. Runner successes count completed session lifecycles; inspect nonzero_commands and timed_out_commands separately. Each command starts a separate shell in /home/user; filesystem changes persist, shell cd/export state does not.
