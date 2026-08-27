# Findings

> **Status: awaiting an authoritative run.**
>
> This page is intentionally empty of conclusions. Authoritative numbers require
> the load generator and the SUT on separate machines
> ([docs/RUNBOOK.md](RUNBOOK.md)); until such a run exists, the honest thing to
> publish here is nothing.
>
> A real but constrained run is committed under
> [`results/local-validation/`](../results/local-validation/). It is directional
> evidence that the harness works, not a benchmark result, and its magnitudes
> should not be quoted.

## What to write here after a two-machine run

Fill each section from `results/<run-id>/report.md`, and keep the numbers
attached to the conditions that produced them.

### 1. Load generator efficiency

The headline: requests per second per client CPU core, per tool, per concurrency
level. State which tool saturated its own CPU first, and at what throughput.

### 2. Saturation point

For each tool, the highest throughput reached before the **server's** p99
exceeded the chosen SLO, or before errors appeared. If a tool never got the
server near saturation, say so plainly — that is the finding.

### 3. What multiplexing was worth

The three ghz rows isolate it:

- `ghz --connections = concurrency` versus JMeter — the tool difference at
  matched topology.
- `ghz --connections = 1 or 8` versus `ghz --connections = concurrency` — what
  HTTP/2 multiplexing adds on top.

Report both differences separately. Collapsing them into one number is the claim
this repository exists to avoid making.

### 4. The `Compute` result

The sharpest test. With a non-blocking server-side delay, a closed-loop tool's
ceiling is roughly `concurrency / delay`. Report whether each tool reached it,
and what the server-side in-flight gauge showed while it tried.

### 5. Costs that are not throughput

- Startup: `time_to_first_request`, which includes the plugin's per-thread
  `protoc` invocation.
- Memory: peak RSS per tool at each concurrency level.
- Connections: peak connections the server observed, against configured
  concurrency.

### 6. Where the benchmark was unfair, if anywhere

Anything noticed during the run that favoured one side, whether or not it was
corrected. A benchmark that reports no such observations has usually not looked.

## Reporting rules

- Every number carries its run id, so it can be traced to a manifest and to raw
  records.
- No number from a single-host run appears here.
- Client-reported figures appear alongside the server's own count for the same
  window; if the two disagree by more than a few percent, the run is re-read
  rather than reported.
- Conclusions name the plugin, not JMeter, unless the evidence genuinely
  concerns JMeter itself.
