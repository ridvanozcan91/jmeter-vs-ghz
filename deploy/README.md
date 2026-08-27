# Container and Kubernetes deployment

> **Not validated.** The environment this repository was developed in had no
> Docker daemon and no Kubernetes cluster, so nothing in this directory has ever
> been built or applied. It is a starting point, not a tested path.
>
> The validated way to run this benchmark is the native two-machine procedure in
> [../docs/RUNBOOK.md](../docs/RUNBOOK.md).

If you do use these, verify before trusting any numbers they produce:

- the load generator and the SUT land on **different nodes** (the anti-affinity
  rule below is what should ensure it — confirm with `kubectl get pods -o wide`);
- each pod actually got the CPU it asked for, and the limits are not throttling
  the load generator in a way that flatters the server;
- the `benchmark_server_connections_active` gauge shows what you expect for each
  tool, which is the quickest way to tell the configuration applied.

Container networking adds a hop that native runs do not have. Measure the
in-cluster network baseline as described in the runbook, and treat container and
native results as separate populations rather than comparing them directly.
