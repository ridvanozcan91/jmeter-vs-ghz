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

Leave `SUT_JAR` unset: with the server on another machine the harness cannot
restart it, so restart it yourself between tools if you want the strictest
isolation, or accept the shared warmup and note it.

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

## Kubernetes

`deploy/k8s/` holds manifests for running the SUT as a Deployment and the load
generators as Jobs, with anti-affinity so they never land on the same node and
explicit resource limits so each gets a known CPU budget.

> These manifests are **not validated** — the environment this repository was
> developed in had no Docker daemon and no cluster, so they were never applied.
> Treat them as a starting point and verify before trusting results from them.
> The native two-machine path above is the one that has been exercised.

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
