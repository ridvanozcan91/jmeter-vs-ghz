# jmeter-vs-ghz

A neutral benchmark harness for comparing **Apache JMeter** (with the
[zalopay `jmeter-grpc-request`](https://github.com/zalopay-oss/jmeter-grpc-request)
plugin, v1.2.5.1) against **[ghz](https://github.com/bojand/ghz)** as load
generators for a Spring Boot gRPC service.

## The question

When load testing a Spring Boot gRPC service with JMeter, the load generator
often saturates before the server does. The suspicion is that this is
architectural — that the gRPC sampler cannot use HTTP/2's ability to keep many
requests in flight on one connection — and that ghz does not have the same
ceiling.

This repository turns that suspicion into a measurement, without stacking the
deck for either tool.

One distinction matters and is kept throughout: what is measured here is a
property of **the plugin**, not of JMeter. JMeter's thread model is a container
for whatever a sampler does; this sampler issues a blocking unary call and holds
one channel per thread. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
source references.

## What makes it neutral

A tool comparison is easy to rig by accident. The rules this harness follows,
all enforced in code:

| | |
|---|---|
| **The server is the referee** | Neither tool is trusted to report on itself. The SUT records its own latency, request counts, in-flight RPCs and open connections, identically for both tools. |
| **Same topology first** | The plugin opens one channel per thread, so ghz is run at `--connections = concurrency` to reproduce that exactly — *then* at fewer connections, so multiplexing is isolated rather than assumed. |
| **One percentile function** | Neither tool's summary is used. Both tools' raw per-request records are reduced to identical columns and scored by the same code, over the same seconds. |
| **Closed and open loop kept apart** | JMeter threads make throughput an *outcome*; `--rps` makes it an *input*. Mixing them in one table is the usual way these comparisons mislead. |
| **Equal tuning effort** | Every setting given to either tool is written down in [docs/TUNING.md](docs/TUNING.md), as a list to argue with. |
| **Medians over repeats, shuffled order** | Each cell runs 3–5 times in randomised tool order; results are medians with spread, never a single run. |

Read [docs/METHODOLOGY.md](docs/METHODOLOGY.md) for the full contract, including
what is deliberately **not** corrected and why.

## The headline metric is not throughput

It is **requests per second per load-generator CPU core**, read alongside
**achieved concurrency** and the server's own saturation signals.

Raw throughput cannot answer the actual question — *did the tool run out before
the server did?* A tool producing 5,000 RPS while pinning three cores has not
beaten one producing 4,000 RPS on a third of a core.

## Results

Authoritative numbers require the load generator and the server to be on
**separate machines**; otherwise they compete for CPU and the tool that uses more
client CPU is penalised twice.

- **[docs/FINDINGS.md](docs/FINDINGS.md)** — awaiting an authoritative two-machine run.
- **[results/local-validation/](results/local-validation/)** — a real run on a
  4-core host with the SUT pinned to 2 cores and the load generator to 1.
  Committed to show the harness works end to end and to give a directional
  signal. Its magnitudes are **not** publishable; the report says so on its face.

## Quick start

```bash
make tools     # pinned ghz + JMeter with the plugin built from its git tag
make build     # the Spring Boot gRPC System Under Test
make test      # harness unit tests (standard library only, no pip install)
make smoke     # proves the pipeline runs end to end
```

Then, for real numbers, follow [docs/RUNBOOK.md](docs/RUNBOOK.md):

```bash
export SUT_HOST=<the-other-machine>
make full
```

`make full` refuses to run against localhost, because a single-host result is not
the thing this benchmark is for.

## What is measured

The SUT ([`proto/benchmark/v1/benchmark.proto`](proto/benchmark/v1/benchmark.proto))
exposes methods shaped to isolate different bottlenecks:

- **`Echo`** — minimal payload; the raw per-RPC overhead of the tool.
- **`Compute(delay_ms)`** — the server delays without blocking a thread, so a
  closed-loop client's throughput is bounded by its own concurrency. This is the
  sharpest test of whether a tool can actually hold requests in flight.
- **`Payload(size)`** — serialization and bandwidth cost.
- **`ServerStream` / `BidiStream`** — streaming, with each tool's real support
  reported honestly.

## Layout

```
proto/       the contract both tools call
sut/         Spring Boot 3 + grpc-spring-boot-starter, Java 21, with its own metrics
loadgen/     one runner per tool, sharing a single workload definition
harness/     normalize, analyze, report, orchestrate — the neutral core
tools/       pinned installers for ghz and JMeter + plugin
deploy/      Docker and Kubernetes (unvalidated; see deploy/README.md)
docs/        methodology, architecture, tuning, runbook, findings
results/     one directory per run: raw output, canonical records, manifest, report
```

## Contributing a challenge

The most useful contribution is an argument that the benchmark is unfair to one
side, ideally with a configuration change that improves it. `docs/TUNING.md` is
the list of everything each tool was given, and every published number can be
recomputed from the raw records committed under `results/`.
