# Running the benchmark on OpenShift

For the common corporate case: you have **one namespace**, you can **push
images**, and you have no cluster-admin rights. Everything here stays inside a
namespace — no SCC changes, no privileged pods, no node access.

Written against OpenShift 4.20 (Kubernetes v1.33). Every API used —
`apps/v1`, `batch/v1`, `networking.k8s.io/v1`, `rbac.authorization.k8s.io/v1`,
`template.openshift.io/v1` — is stable there, and none of it needs a version
that recent.

> **What a shared cluster can and cannot answer.** The two-machine procedure in
> [RUNBOOK.md](RUNBOOK.md) exists because a load generator and a server that
> share CPU produce numbers that exaggerate the difference between tools. A
> cluster fixes that — anti-affinity puts them on separate nodes — and
> introduces a different problem: the nodes are not yours. Other workloads move
> around you, CPU limits throttle rather than pin, and unless your cluster runs
> the static CPU Manager policy (a cluster-level setting you almost certainly
> cannot check or change from a namespace) no pod gets exclusive cores.
>
> Cluster results are worth producing. Treat them as **the ordering plus a
> spread**, not as absolute magnitudes, and report them with the node names,
> the CPU limits, and the interquartile range visible. The "sanity checks"
> section below is what tells you whether a given run cleared that bar.

## 0. What your namespace actually allows

Check before building anything; these four commands decide the shape of the run.

```bash
oc project                      # you are in the right namespace
oc describe quota               # how much CPU and memory you may request
oc describe limitrange          # per-container caps and defaults
oc get nodes                    # you need at least two schedulable worker nodes
```

If `oc get nodes` is forbidden — common in locked-down clusters — you can still
proceed. The anti-affinity rule will simply leave the load generator `Pending`
if there is only one node available to you, which is itself the answer.

The defaults below ask for **4 CPU / 6Gi** for the server and **4 CPU / 8Gi**
for the load generator. If your quota is smaller, see
[Fitting a small quota](#fitting-a-small-quota) rather than silently shrinking
both sides.

## 1. Build and push the images

Two images: the server, and one containing both load generators. Build them
where you have a container engine and network access, then push.

```bash
# --platform matters if you build on an Apple Silicon laptop: the cluster is
# almost certainly amd64, and an arm64 image fails to start with an exec
# format error that reads like a broken entrypoint.
podman build --platform linux/amd64 -f deploy/sut.Dockerfile \
  -t benchmark-sut:1 .

# GIT_COMMIT is baked in because a container has no git checkout, and every run
# manifest records the commit that produced the numbers.
podman build --platform linux/amd64 -f deploy/loadgen.Dockerfile \
  --build-arg GIT_COMMIT="$(git rev-parse HEAD)" \
  -t benchmark-loadgen:1 .
```

The load generator build compiles ghz from the Go module proxy and the JMeter
plugin from its git tag, so the build host needs to reach `proxy.golang.org`,
`repo.maven.apache.org`, `archive.apache.org` and `github.com`. Building outside
the cluster is deliberate: it keeps that dependency off your namespace.

Then push, either to the cluster's own registry:

```bash
NS=$(oc project -q)
REG=$(oc get route default-route -n openshift-image-registry \
        -o jsonpath='{.spec.host}' 2>/dev/null)

oc registry login --registry="$REG"
podman push benchmark-sut:1      "$REG/$NS/benchmark-sut:1"
podman push benchmark-loadgen:1  "$REG/$NS/benchmark-loadgen:1"
```

or to your company registry (Quay, Harbor, Artifactory), which is the path to
take when the internal registry route is not exposed:

```bash
podman push benchmark-sut:1     registry.corp.example/team/benchmark-sut:1
podman push benchmark-loadgen:1 registry.corp.example/team/benchmark-loadgen:1

oc create secret docker-registry corp-registry \
  --docker-server=registry.corp.example \
  --docker-username=<user> --docker-password=<token>
oc secrets link default           corp-registry --for=pull
oc secrets link benchmark-loadgen corp-registry --for=pull   # after step 2
```

Inside the cluster, images pushed to the internal registry are referenced as
`image-registry.openshift-image-registry.svc:5000/$NS/<name>:<tag>`.

**Pin by digest.** A tag can be overwritten between the run and the reading of
it, and then the manifest names an image that no longer exists:

```bash
skopeo inspect --format '{{.Digest}}' docker://registry.corp.example/team/benchmark-loadgen:1
# use registry.corp.example/team/benchmark-loadgen@sha256:… as IMAGE below
```

<details>
<summary>Alternative: build inside the cluster</summary>

If you cannot push images but can create BuildConfigs, OpenShift can build from
a local directory. This only works if your cluster's builds have egress to the
public artifact repositories listed above.

```bash
oc new-build --name benchmark-loadgen --binary --strategy=docker
oc patch bc/benchmark-loadgen --type=json -p \
  '[{"op":"add","path":"/spec/strategy/dockerStrategy/dockerfilePath","value":"deploy/loadgen.Dockerfile"}]'
oc start-build benchmark-loadgen --from-dir=. --follow
```

The resulting ImageStream tag is usable directly as `IMAGE`.
</details>

## 2. Create the namespace objects

```bash
NS=$(oc project -q)
SUT_IMAGE=image-registry.openshift-image-registry.svc:5000/$NS/benchmark-sut:1
LOADGEN_IMAGE=image-registry.openshift-image-registry.svc:5000/$NS/benchmark-loadgen:1

# Service account and a pods get/list/delete Role, nothing wider.
oc apply -f deploy/openshift/rbac.yaml

# Where results are written. Size it for the profile you intend to run.
oc apply -f deploy/openshift/results-pvc.yaml

# The server.
oc process -f deploy/openshift/sut.yaml -p IMAGE="$SUT_IMAGE" | oc apply -f -

# Only if your namespace has a default-deny ingress policy — check first.
oc get networkpolicy
oc apply -f deploy/openshift/networkpolicy.yaml
```

### Why the load generator gets permission to delete a pod

The matrix restarts the server between tools so that neither tool inherits JIT
compilation paid for by the other. On one host the harness owns the JVM and
kills it; in a cluster the equivalent is deleting the SUT pod and letting the
Deployment replace it — which is all
[`harness/k8s_restart_sut.py`](../harness/k8s_restart_sut.py) does, using the
pod's own service account token and the standard library.

If you cannot create Roles, pass `-p SUT_RESTART_CMD=` (empty) to the Job below.
The run still works, and the deviation is a real one: whichever tool runs second
in each cell meets a server the first tool warmed up. Say so when you report the
numbers.

## 3. Preflight — five minutes that save five hours

Do not start a multi-hour matrix without proving the path works. This catches
every failure mode that looks like a tool result but is not one.

```bash
oc run preflight --image="$LOADGEN_IMAGE" --restart=Never --command -- sleep 3600
oc rsh preflight
```

Inside the pod:

```bash
# 1. Is the server reachable and healthy through the Service?
curl -sf http://benchmark-sut:9091/actuator/health && echo OK

# 2. Does gRPC actually answer? A NetworkPolicy problem shows up here as
#    UNAVAILABLE, which reads exactly like a tool failure and is not one.
.tools/bin/ghz --insecure \
  --proto proto/benchmark/v1/benchmark.proto --import-paths proto \
  --call benchmark.v1.BenchmarkService/Echo -d '{"message":"hi"}' \
  -n 200 -c 8 benchmark-sut:9090 | head -20

# 3. Does JMeter start, and did the plugin load? An empty CSV here means the
#    plugin is missing; an error about the proto folder means a path problem.
loadgen/jmeter/run.sh echo 4 5 /tmp/preflight && head -3 /tmp/preflight/jmeter.csv

# 4. What CPU budget did this pod really get, and is it being throttled?
cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/cpu.stat
```

Then clean up: `exit` and `oc delete pod preflight`.

## 4. Run the matrix

Start with the smoke profile. It proves the whole chain — both tools, the
restart hook, normalization, analysis, the report — in a couple of minutes, and
its numbers are meaningless by design.

```bash
oc process -f deploy/openshift/loadgen-job.yaml \
  -p IMAGE="$LOADGEN_IMAGE" -p RUN_ID=smoke-1 \
  -p PROFILE=smoke -p FAMILY=closed | oc apply -f -

oc logs -f job/benchmark-loadgen-smoke-1
```

While it runs, confirm the thing the whole cluster setup is for:

```bash
oc get pods -o wide -l 'app in (benchmark-sut,benchmark-loadgen)'
```

The two pods must show **different NODE values**. If the load generator is
`Pending`, `oc describe pod` will say the anti-affinity rule could not be
satisfied — see the troubleshooting table.

Then the real run. `RUN_ID` becomes part of the Job name and the results
directory, so it must be lowercase and DNS-safe:

```bash
RUN_ID=$(date -u +%Y%m%dt%H%M%Sz)

oc process -f deploy/openshift/loadgen-job.yaml \
  -p IMAGE="$LOADGEN_IMAGE" -p RUN_ID="$RUN_ID" \
  -p PROFILE=full -p FAMILY=closed \
  -p CONCURRENCY_LEVELS="8 32 128 512" \
  -p METHODS="echo compute" \
  -p REPEATS=3 | oc apply -f -
```

### How long it takes

Each run is `warmup + measure + cooldown` (30 + 60 + 15s by default) plus a SUT
restart and a warmup pass, so budget roughly **three minutes per run**. The
number of runs is the matrix:

| Family | Runs | Wall clock |
|---|---|---|
| Closed loop, full defaults (3 methods × 7 concurrency levels × 4 tool variants × 5 repeats) | 420 | ~20 h |
| Closed loop, trimmed as above (2 × 4 × 4 × 3) | 96 | ~5 h |
| Open loop, full defaults (6 rates × 2 tools × 5 repeats) | 60 | ~3 h |
| Smoke | 2 | ~2 min |

`--family all` runs both families. The trimmed run above is the one to do first:
it covers the interesting shape of the curve, and you will learn more from
finishing it than from a 20-hour run that gets evicted at hour 14.

Keep the concurrency **levels** you cut honest: dropping the top of the sweep
removes exactly the region where a load generator ceiling would appear, so say
which levels you ran rather than presenting a trimmed sweep as the full one.

## 5. Get the results out

```bash
oc process -f deploy/openshift/results-shell.yaml | oc apply -f -
oc rsync benchmark-results-shell:/results/$RUN_ID ./results/
oc delete pod benchmark-results-shell
```

`results/$RUN_ID/report.md` is already rendered inside the pod. To recompute it
locally from the raw records — which is the point of keeping them — see step 4
of [RUNBOOK.md](RUNBOOK.md).

The claim is ReadWriteOnce, so the shell pod stays `Pending` until the Job's pod
has finished. That is correct behaviour, not a fault.

## 6. Sanity checks before believing anything

Everything in [RUNBOOK.md § 5](RUNBOOK.md#5-sanity-checks-before-believing-anything)
applies unchanged. On a cluster, three more:

- **Different nodes.** `manifest.json` records both node names, and the report
  prints a banner if they match. A run where they match is directional at best.
- **No CPU throttling.** The report prints a banner when any run was throttled
  in more than 2% of its scheduling periods, using the cgroup counters recorded
  alongside each run. A throttled tool reports the quota's ceiling, not its own,
  which would turn a `limits.cpu` value into a finding about JMeter or ghz.
  Raise `CPU` and rerun.
- **Spread.** A busy cluster shows up as a wide interquartile range across
  repeats. Compare the IQR to the difference you are claiming; if they are the
  same size, you have measured the cluster.

Also worth recording next to the numbers, because none of it is visible in the
report: whether the nodes were shared with other workloads, whether the SUT
restart was enabled, and the CPU limits both pods ran under.

## Fitting a small quota

If the namespace cannot give both pods 4 CPU, the wrong fix is to shrink both
until it fits and carry on. The choice is between two different measurements:

- **Give the server the most CPU.** The benchmark is about which load generator
  runs out first, and it can only answer that if the server does not. With, say,
  6 CPU total: 4 to the SUT, 2 to the load generator, and expect the ceiling you
  find to be the load generator's — which is the question, as long as the report
  shows no throttling and you state the limit.
- **Lower the concurrency levels** instead of the CPU. A 512-thread JMeter run
  in a 2-CPU pod measures the pod. Cut the sweep at the point where the client
  saturates and say where you cut it.

Set both sides explicitly so the numbers can be read later:

```bash
oc process -f deploy/openshift/sut.yaml -p IMAGE="$SUT_IMAGE" \
  -p CPU=4 -p MEMORY=6Gi -p HEAP="-XX:+UseG1GC -Xms4g -Xmx4g" | oc apply -f -

oc process -f deploy/openshift/loadgen-job.yaml -p IMAGE="$LOADGEN_IMAGE" \
  -p RUN_ID="$RUN_ID" -p CPU=2 -p MEMORY=4Gi \
  -p JMETER_HEAP="-Xms1g -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=100" \
  -p CONCURRENCY_LEVELS="8 32 128" | oc apply -f -
```

Heap must fit inside the memory limit with room for the JVM's own overhead. A
container whose heap equals its limit is a container that gets OOMKilled
mid-run, and an OOMKill halfway through a measured window looks like a tool that
collapsed under load.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Load generator pod `Pending`, events mention node affinity | Only one node is schedulable for you, so the anti-affinity rule cannot be satisfied | Get a second node, or switch the rule to `preferredDuringScheduling` and accept a directional result — the report will flag the shared node |
| Pod `Pending`, events mention `exceeded quota` | The namespace quota is smaller than the request | See [Fitting a small quota](#fitting-a-small-quota) |
| `ImagePullBackOff` | The registry needs credentials the service account does not have | `oc secrets link benchmark-loadgen <secret> --for=pull` (and `default` for the SUT) |
| `CrashLoopBackOff`, log shows a permission error on `/app` | An image that assumes it runs as a fixed user; OpenShift assigns an arbitrary UID | Both Dockerfiles here already `chgrp -R 0` and `chmod -R g=u`; if you modified them, keep that |
| `exec format error` | Image built for arm64 on a laptop | Rebuild with `--platform linux/amd64` |
| Every request fails `UNAVAILABLE`, SUT healthy | Default-deny NetworkPolicy | `oc apply -f deploy/openshift/networkpolicy.yaml` |
| `restart failed: … HTTP 403` in `sut-restart.log` | The Role or RoleBinding is missing | `oc apply -f deploy/openshift/rbac.yaml`, or run with `-p SUT_RESTART_CMD=` and note the deviation |
| SUT pod restarts mid-run | OOMKill (heap too close to the limit), or an eviction | Check `oc describe pod`; lower `HEAP` or raise `MEMORY` |
| Report shows a throttling banner | The load generator hit its cgroup CPU quota | Raise `CPU`; the affected numbers describe the limit, not the tool |
| `oc rsync` fails with "cannot exec" | The Job pod has finished; a completed pod cannot be exec'd into | Use the results-shell pod in step 5 |

## What this path does not give you

- **Exclusive cores.** Guaranteed QoS pins a budget, not specific CPUs, unless
  the cluster runs the static CPU Manager policy. Neighbouring workloads still
  affect cache and memory bandwidth.
- **A quiet network.** Pod-to-pod traffic crosses the SDN and whatever else the
  nodes are carrying. Measure the baseline — the preflight ghz run above gives
  you a floor — and note it.
- **Node-level tuning.** `somaxconn` and the file descriptor limits recorded in
  the manifest come from the node, and you cannot change them from a namespace.

None of these invalidate a comparison in which both tools ran under identical
constraints, back to back, in shuffled order. They do mean the magnitudes belong
to that cluster on that day, and the honest way to publish them says so.
