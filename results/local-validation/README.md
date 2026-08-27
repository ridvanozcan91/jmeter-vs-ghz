# Local validation run

**These magnitudes are not publishable.** The load generator and the server ran
on the same 4-core host, pinned to disjoint CPU sets (SUT on cores 0-1, load
generator on core 2). They still shared memory bandwidth and last-level cache,
and one core is far too little to push either tool to its real ceiling.

What this run is for:

1. Proving the harness works end to end — 66 runs, both tools, both families,
   zero warnings, and client/server counts agreeing to a median of 2.1%.
2. Establishing the *direction and mechanism* of the difference, which does not
   depend on having enough hardware.

What it is not for: any absolute number. Those come from the two-machine
procedure in [../../docs/RUNBOOK.md](../../docs/RUNBOOK.md), and until such a
run exists [../../docs/FINDINGS.md](../../docs/FINDINGS.md) stays empty of
conclusions.

## Configuration

| | |
|---|---|
| Host | 4-core Intel Xeon @ 2.10GHz, 16 GB |
| SUT | cores 0-1, `-Xms1g -Xmx2g`, G1 |
| Load generator | core 2 |
| Window | 8s warmup, 15s measured, 3 repeats per cell |
| Concurrency | 8, 32, 128 (256 for the open-loop family) |
| Methods | `echo`, `compute(50ms)` |

## What the mechanism looks like

The connection table is the part that transfers to any hardware, because it is
counted by the server and describes topology rather than speed. At concurrency
128 on `compute`:

| Tool | Connections held | RPCs in flight |
|---|---:|---:|
| JMeter (plugin) | 128 | 105 |
| ghz, 1 connection | 1 | 128 |

One connection carrying 128 concurrent RPCs is HTTP/2 multiplexing doing its
job. 128 connections carrying 105 is one blocking call per channel, which is
the ceiling described in [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).

On `echo`, the plugin's in-flight count sits at 0-1 even with 128 threads. Its
threads are almost never waiting on the server: they are busy on the client,
rebuilding each request message from JSON and being scheduled. That is the
shape of a load generator that has become the bottleneck.

## Reproducing

Raw and canonical per-request records are not committed; see
[../../docs/METHODOLOGY.md](../../docs/METHODOLOGY.md). To regenerate the report
from what is here:

```bash
python3 harness/report.py --results-dir results/local-validation \
  --output results/local-validation/report.md
```
