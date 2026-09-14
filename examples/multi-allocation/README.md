# One experiment across independent jobs

This scheduling demonstration creates one experiment containing a CPU service,
two one-L40 judge placeholders, four one-H200 policy placeholders, and an evaluator.
Commands print their experiment/replica identity and sleep; they do not run models.

Configure the three profiles for your account, partitions, QoS, and a Python-capable
Apptainer image. Then validate without allocating resources:

```sh
rexs validate examples/multi-allocation/experiment.yaml --strict
```

Submit once when ready:

```sh
rexs submit examples/multi-allocation/experiment.yaml --strict --name grouped-demo
rexs show EXPERIMENT_ID
rexs logs EXPERIMENT_ID --task=policy --replica=1
rexs cancel EXPERIMENT_ID
```

The evaluator sleeps for 30 seconds, then its cleanup trap cancels every other
allocation, including queued jobs. There are no application readiness gates in
this demonstration. For real models, replace the commands and add service
readiness checks to the evaluator before starting work. REXS does not wait for
all replicas to start before submitting evaluation.
