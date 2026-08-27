# Methodology

A benchmark between two tools is easy to rig by accident. This document states
what this harness does to avoid that, in enough detail that a reader who
distrusts the conclusion can attack the method rather than guess at it.

## The fairness contract

Eight rules. All are enforced in code; where a rule is enforced by a test, the
test is named.

### 1. No tool is trusted to report on itself

The System Under Test measures itself. A `ServerInterceptor` records handling
latency, request counts and in-flight RPCs; a `ServerTransportFilter` counts
connections. Both are identical for every load generator because neither tool
can see or influence them.

Every claim in a report that could favour one tool is checked against these
server-side numbers. In particular, "tool X reached N requests per second" is
only published alongside the server's own count for the same window.

### 2. Closed loop and open loop are never mixed

With the plugin, JMeter is a *closed-loop* generator: you set thread count and
throughput is whatever comes out. ghz with `-c N` and no rate limit is closed
loop too, which makes the two directly comparable. ghz with `--rps` is
*open loop*: throughput is an input and queueing is allowed to build.

These are different experiments. Comparing "JMeter with 100 threads" against
"ghz at 10,000 RPS" measures nothing, and is the most common way published gRPC
tool comparisons mislead. The harness keeps them as separate families
(`--family closed`, `--family open`) and the report never puts them in one table.

### 3. Connection topology is controlled, not assumed

The plugin opens one channel per thread. Left alone, ghz would open one for all
workers, so a naive comparison would conflate "ghz is more efficient" with "ghz
was allowed to multiplex".

So ghz is run at `--connections = concurrency` first — the plugin's exact
topology — and only then at smaller connection counts. The difference between
those two ghz rows is what multiplexing is worth; the difference between the
matched ghz row and JMeter is what the rest of the tool is worth.

### 4. Percentiles are recomputed from raw records, by one function

Neither tool's summary output is used. Both tools' per-request records are
reduced to the same columns by `harness/normalize.py`, and every statistic is
computed by `harness/analyze.py` using nearest-rank percentiles over the same
sorted array.

Two asymmetries are corrected during normalization:

- **Timestamp semantics.** ghz records the RPC end time; JMeter records the
  sample start. Both become an explicit `start_ns`/`end_ns` pair.
  (`test_both_tools_agree_on_a_request_spanning_the_same_wall_clock`)
- **Status vocabulary.** ghz reports gRPC status names; the plugin reports an
  HTTP-flavoured code plus a success flag. Both become one `ok` boolean, with
  the raw status kept for auditing.

### 5. The measured window is the same seconds for both tools

Each run is longer than the window it is scored on. The window is cut from raw
timestamps: a request counts if it *completed* inside it. The server counts
completions the same way, which is what makes the client/server agreement check
in each report meaningful.

Warmup exists because two JVMs are involved — the server always, and JMeter's
own. `--skipFirst` is deliberately not used, because it skips a fixed *count*
rather than a fixed *duration*, which would give the faster tool a longer real
warmup.

### 6. Equal tuning effort

Every setting applied to either tool is written down in [TUNING.md](TUNING.md).
JMeter runs headless with listeners off, a generously sized heap and a trimmed
result format; ghz runs with templating disabled and its deadline matched to
JMeter's. If you think one side is under-tuned, that document is the list to
argue with.

### 7. Repeats, shuffled order, and medians

Each cell is run at least three times (five in the full profile). Tool order is
shuffled within each round from a seed derived from the run id, so a machine
that warms up or gets busier over a session does not systematically favour
whichever tool always went first. Reported figures are medians with an
interquartile range, never a single run and never a mean — one GC pause should
not move a headline number.
(`test_median_not_mean_across_repeats`)

The SUT is restarted between tools and given an identical warmup, so no tool
inherits JIT compilation the other paid for.

### 8. What is not corrected, and why

Correcting these would mean measuring something other than the tools:

- **Timer resolution.** JMeter's `elapsed` is integer milliseconds; ghz's is
  nanoseconds. For sub-millisecond responses JMeter simply cannot describe the
  latency distribution. Reports carry the resolution per row and say to read
  throughput instead of latency shape for `echo`.
- **Runtime differences.** JMeter runs on the JVM with GC pauses and thread
  scheduling; ghz is a Go binary with goroutines. That *is* the difference under
  test.
- **Per-request marshalling.** The plugin rebuilds its request message from JSON
  on every call; ghz builds it once. This is a real cost of using the plugin and
  is reported rather than equalised. For a run where ghz is made to do
  comparable per-call work, enable templating and note it in the run manifest.
- **Startup cost.** The plugin invokes `protoc` once per thread. It is excluded
  from the measured window by the warmup, and reported separately as
  `time_to_first_request`.

## The headline metric

Not throughput. **Requests per second per load-generator CPU core**, alongside
**achieved concurrency** and the **server's own saturation signals**.

Raw throughput cannot answer the question that matters — *did the tool run out
before the server did?* — and that question is the entire reason this repository
exists. A tool producing 5,000 RPS while pinning three cores has not beaten one
producing 4,000 RPS on a third of a core.

Achieved concurrency (Little's law over the window) is the second signal: a
closed-loop tool whose achieved concurrency sits well below its configured level
is a tool that could not keep its own workers busy.

## Threats to validity

Stated so they can be argued with:

- **Coordinated omission.** Closed-loop measurement hides latency under
  saturation, because a stalled worker stops issuing requests. This is why the
  open-loop family exists; read the two together.
- **Single-host runs.** If the load generator and server share a host they
  compete for CPU, and the tool that uses more client CPU is penalised twice.
  Reports generated in that configuration carry a warning banner, and
  [RUNBOOK.md](RUNBOOK.md) describes the two-machine procedure that avoids it.
- **Clock skew across hosts.** Client-side latency is measured on the client's
  clock and server-side latency on the server's. The manifest records NTP offset
  where available; the two are compared as distributions, never subtracted
  per-request.
- **Server-side sampling rate.** The server is polled twice a second, so window
  edges round to the nearest poll. Reports show the resulting drift explicitly
  rather than hiding it; a few percent is expected, a large gap means the run
  should be re-read.
- **Version pinning.** Findings apply to the pinned versions in the manifest.
  Plugin v1.2.5.1 was chosen because it is what the request specified; later
  versions exist and may behave differently.
- **Build environment.** Plugin v1.2.5.1 declares Lombok 1.18.24, which cannot
  compile under JDK 21. The build overrides the Lombok version only. No plugin
  source is modified, and the override is recorded in
  [TUNING.md](TUNING.md).

## Reproducing a result

Each run directory keeps the raw tool output, the canonical per-request records,
the client resource samples, the server metric series, and a manifest recording
versions, hardware, kernel, ulimits and scenario settings.

**Not all of it is committed.** A single run produces hundreds of megabytes,
because ghz emits a JSON object per request, so `results/*/raw/` and
`results/*/normalized/` are git-ignored. What is committed is the derived layer:
`manifest.json`, `analysis.json` (per-run and per-scenario statistics), the
report and its charts.

That means a published number can be traced to the exact environment and
settings that produced it, and the report can be regenerated from
`analysis.json`, but recomputing percentiles from individual requests needs the
records themselves. `harness/pack-results.sh results/<run-id>` archives them for
exactly that purpose; ask for the archive of any run whose numbers you want to
audit from first principles.

Regenerating the report from committed data alone:

```bash
python3 harness/report.py --results-dir results/<run-id> \
  --output results/<run-id>/report.md
```
