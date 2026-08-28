# Runbook

How to produce authoritative numbers. The short version: **the load generator
and the server must not share a host.** Everything else here is detail.

## Why two machines

A load generator competes with the server for CPU, cache and memory bandwidth.
On a shared host, the tool that uses more client CPU is penalised twice — once
for its own inefficiency, and again for the server slowdown it causes. That
turns a real difference into an exaggerated one, which is worse than useless
for a benchmark meant to be neutral.

Single-host runs are still useful for developing the harness and for a quick
directional check. Reports produced that way carry a warning banner
automatically, driven by `target.same_host_as_loadgen` in the manifest.

## Prerequisites

On both machines:

- JDK 21
- `curl`, `python3` (3.9+, standard library only)

On the load generator:

- Go 1.21+ (to install ghz), or a ghz binary
- Maven (to build the JMeter plugin from source)

```bash
tools/install-ghz.sh      # installs a pinned ghz into ./.tools/bin
tools/install-jmeter.sh   # installs JMeter and builds the plugin from its tag
```

Both scripts pin versions and print the sha256 of what they installed. Those
hashes end up in every run manifest.

## Procedure

### 1. Start the SUT on machine A

```bash
cd sut && mvn -DskipTests package
java -XX:+UseG1GC -Xms4g -Xmx4g -jar target/benchmark-sut-1.0.0.jar
```

Check it is reachable *from machine B*, not just locally:

```bash
curl -f http://<machine-A>:9091/actuator/health
```

Give the server more CPU than you expect the load generator to need. The server
must never be the bottleneck; if it is, the benchmark measures the server. The
`benchmark_server_rpcs_in_flight` gauge and JVM CPU metrics tell you whether it
came close.

### 2. Measure the network baseline from machine B

Latency between the hosts is part of every client-side measurement, so record it
before the matrix rather than guessing afterwards:

```bash
ping -c 100 <machine-A> | tail -3
```

Note the median RTT. Client-observed latency minus server-observed latency minus
RTT is the tool's own overhead, which is the number worth comparing.

Also check that the clocks are close. Large skew does not corrupt latency
measurements (each side uses its own clock for its own durations) but it does
shift the window alignment between client records and server samples.

### 3. Run the matrix from machine B

```bash
export SUT_HOST=<machine-A>
export GHZ_BIN=$PWD/.tools/bin/ghz
export JMETER_HOME=$PWD/.tools/apache-jmeter-5.6.3
export RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)

harness/run-matrix.sh --profile full --family all
```

Leave `SUT_JAR` unset: the harness only starts a JVM it owns. To keep the
between-tools restart on a two-machine setup, give it a command that does the
restart remotely — `SUT_RESTART_CMD="ssh machine-A systemctl restart benchmark-sut"`,
say — and the harness will run it and wait for health before each tool. Without
one, accept the shared warmup and note it.

Leave `SUT_CPUS` and `LOADGEN_CPUS` unset too — CPU pinning exists for the
single-host case.

The full profile is long. It runs 7 concurrency levels times 3 methods times 4
tool variants times 5 repeats, each with warmup, measurement and cooldown.
Budget several hours and run it on an otherwise idle pair of machines.

### 4. Read the output

```
results/<run-id>/
├─ manifest.json      versions, hardware, kernel, ulimits, scenario settings
├─ analysis.json      per-run and per-scenario statistics
├─ report.md          tables and charts
├─ charts/            SVG, regenerable from analysis.json
├─ normalized/        canonical per-request records, one CSV per run
└─ raw/<run-tag>/     raw tool output, client resource samples, server series
```

Regenerate the report without re-running the load:

```bash
python3 harness/analyze.py --canonical-dir results/<run-id>/normalized \
  --output results/<run-id>/analysis.json --warmup-seconds 30 --measure-seconds 60
python3 harness/report.py --results-dir results/<run-id> \
  --output results/<run-id>/report.md
```

### 5. Sanity checks before believing anything

Work through these; each one has caught a bad run:

- **Client/server agreement** within a few percent per run. A large gap means
  the window is misaligned, not that a tool is fast.
- **Error rate at or near zero.** A tool that fails requests looks fast; check
  the error breakdown in `analysis.json` before comparing throughput.
- **Server not saturated.** If server CPU is pinned, you measured the server.
  Give it more cores and rerun.
- **Load generator CPU below saturation for at least one tool.** If both tools
  pin the client, the ceiling you found is the client's, not the tools'. That is
  a legitimate finding, but say so explicitly.
- **Connection counts as expected.** JMeter's should track thread count; ghz's
  should match `--connections`. If not, something did not apply.
- **IQR small relative to the median.** Wide spread means a noisy machine.
  Re-run on an idle host before reporting.

## OpenShift and Kubernetes

[OPENSHIFT.md](OPENSHIFT.md) is the cluster procedure: build and push two
images, run the SUT as a Deployment and the matrix as a single Job, with
anti-affinity so they never share a node and explicit CPU limits so each side
has a known budget. It is written for a single namespace with no cluster-admin
rights, and the manifests in `deploy/openshift/` render to plain Kubernetes
objects for non-OpenShift clusters.

A cluster solves the problem this runbook exists for — the two sides stop
sharing CPU — and introduces its own: the nodes are shared with other
workloads, and CPU limits throttle rather than pin. The harness records both
node names and the cgroup throttling counters for every run, and the report
refuses to present a run as authoritative when the pods shared a node or the
load generator hit its quota.

> Both images have been built and the smoke matrix has run between two
> containers as an arbitrary UID, which is what the restricted SCC imposes.
> Nothing cluster-side — templates, RBAC, anti-affinity, the claim — has been
> applied from this repository's development environment, which has no cluster.
> See `deploy/README.md` for exactly what was and was not exercised, and run the
> preflight in OPENSHIFT.md before a long run.

## Single-host runs

For harness development, or a quick directional check:

```bash
export SUT_JAR=$PWD/sut/target/benchmark-sut-1.0.0.jar
export SUT_CPUS=0,1 LOADGEN_CPUS=2
harness/run-matrix.sh --profile local --family closed
```

Pinning is the minimum needed to stop the load generator from stealing the
server's cores. The resulting report says on its face that its magnitudes are
not authoritative, and that statement should survive into any summary of it.
