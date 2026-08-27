# Container and cluster deployment

Two images and one set of namespace-scoped manifests:

```
sut.Dockerfile        the System Under Test
loadgen.Dockerfile    JMeter with the plugin, ghz, and the harness, in one image
docker-compose.yml    single-host composition, for developing the harness only
openshift/            the cluster path: templates, RBAC, claim, builds
                      — see ../docs/OPENSHIFT.md
```

> **What has been exercised, and what has not.** Both images have been built
> and run: the SUT and the load generator were started as containers on one
> host, each as an arbitrary UID in group 0 with no entry in `/etc/passwd`,
> which is the condition OpenShift's restricted SCC imposes. The smoke matrix
> ran end to end from inside the load generator container — both tools, the
> shuffle, normalization, analysis and the report. The load generator was given
> a 2-core cgroup quota, and the report's throttling banner fired on the JMeter
> run, which was throttled in 73% of its scheduling periods while ghz was not.
>
> Two substitutions were forced by the build environment and are **not** part of
> these Dockerfiles: its egress policy blocks Docker Hub's blob CDN, so the base
> images came from `mcr.microsoft.com`, and Maven was installed with `apt`
> rather than inherited from the `maven` image. Build these files as they are
> and you get the pinned upstream bases.
>
> Nothing cluster-side has been applied — no `oc process`, no RBAC, no
> anti-affinity, no claim, no NetworkPolicy, and no in-cluster build. Those are
> what the preflight in [../docs/OPENSHIFT.md](../docs/OPENSHIFT.md) checks, and
> the reason it exists.
>
> The validated way to produce authoritative numbers remains the native
> two-machine procedure in [../docs/RUNBOOK.md](../docs/RUNBOOK.md): a cluster
> answers the question about the tools, but on hardware you do not control.

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
