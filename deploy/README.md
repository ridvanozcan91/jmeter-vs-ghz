# Container and cluster deployment

Two images and one set of namespace-scoped manifests:

```
sut.Dockerfile        the System Under Test
loadgen.Dockerfile    JMeter with the plugin, ghz, and the harness, in one image
docker-compose.yml    single-host composition, for developing the harness only
openshift/            the cluster path — see ../docs/OPENSHIFT.md
```

> **Not built or applied here.** The environment this repository is developed in
> has no container engine and no cluster, so neither image has been built and no
> manifest has been applied. What is checked is that the manifests parse and
> that the harness pieces they invoke run. Work through the preflight in
> [../docs/OPENSHIFT.md](../docs/OPENSHIFT.md) before starting a long run; it
> exists to catch what an unbuilt Dockerfile can still get wrong.
>
> The validated way to produce authoritative numbers remains the native
> two-machine procedure in [../docs/RUNBOOK.md](../docs/RUNBOOK.md).

## Why the manifests are OpenShift templates

`openshift/` holds `template.openshift.io/v1` Templates because the values that
have to change — image references, CPU and memory budgets, which slice of the
matrix to run — are exactly the values that differ between one namespace and
the next, and a template makes them arguments instead of edits.

The objects inside are plain Kubernetes: Deployment, Service, Job, PVC, Role,
NetworkPolicy. On a cluster without the template API, render them first and
apply the result:

```bash
oc process -f deploy/openshift/sut.yaml -p IMAGE=… | kubectl apply -f -
```

There is deliberately no second, generic `k8s/` copy. Two sets of manifests for
the same deployment drift, and the one that drifts is always the one nobody ran.

## What to verify before trusting numbers from a cluster

- the load generator and the SUT land on **different nodes** — the anti-affinity
  rule is what should ensure it, and `manifest.json` records both node names so
  the report can flag a run where they matched;
- neither pod was **CPU-throttled** — the report prints a banner when a run hit
  its cgroup quota, because a throttled tool reports the limit's ceiling rather
  than its own;
- the `benchmark_server_connections_active` gauge shows what you expect for each
  tool, which is the quickest way to tell the configuration applied.

Container networking adds a hop that native runs do not have. Measure the
in-cluster baseline as described in the runbook, and treat container and native
results as separate populations rather than comparing them directly.
