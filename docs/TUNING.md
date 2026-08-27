# Tuning applied to each tool

Every setting either tool receives is listed here. The purpose is auditability:
if you believe one side was handicapped, this is the page to argue with, and a
pull request that improves either side's configuration is welcome.

The rule the harness follows: **any advantage given to one tool must have an
equivalent on the other, or be documented here as impossible.**

## Shared, identical for both

| Setting | Value | Why it must match |
|---|---|---|
| Target | same host, port, plaintext | TLS on one side only would measure encryption, not the tools |
| Method and payload | built by `loadgen/common/workload.sh` | one code path, so the bytes on the wire cannot drift |
| Per-request deadline | `REQUEST_TIMEOUT_MS` (default 20s) | neither tool gets a longer rope before giving up |
| Max inbound message size | 16 MB | the `payload` method must not fail on one side only |
| Warmup and measured window | `WARMUP_SECONDS` / `MEASURE_SECONDS` | the same seconds are scored for both |
| Repeats | `REPEATS` | equal sample size |

## JMeter

| Setting | Value | Rationale |
|---|---|---|
| Mode | non-GUI (`-n`) | the GUI is not a load generator and nobody runs it that way |
| Listeners | none in the plan | listeners charge result rendering to the sampling thread |
| Result format | CSV, minimal fields | keeps result writing off the critical path; the harness only needs timestamp, elapsed, success and status |
| `sampleresult.timestamp.start` | `true` (explicit) | fixes `timeStamp` as the sample start so normalization cannot drift with a properties file |
| Heap | `-Xms2g -Xmx4g` | large enough that no concurrency level in the matrix is GC-bound |
| GC | G1, `MaxGCPauseMillis=100` | a stable collector, same flags for every run |
| Summariser | disabled | periodic console summarising costs CPU on the load generator |
| Thread group | plain, ramp-up 0, scheduler on | throughput must be an outcome, not shaped by a timer, in the closed-loop family |

Not applied, and why:

- **`--skipFirst`-style trimming.** JMeter has no equivalent, and the harness
  cuts the window from timestamps for both tools instead.
- **Distributed mode.** Running JMeter across several injectors would raise its
  ceiling, but ghz would then deserve several instances too. Both are
  single-instance here; scaling either out is a separate experiment.
- **A different gRPC sampler.** Out of scope by request: this benchmark covers
  the zalopay plugin at v1.2.5.1. See [ARCHITECTURE.md](ARCHITECTURE.md) for why
  that distinction matters to the conclusion.

### Build note

Plugin v1.2.5.1 declares Lombok 1.18.24, which fails to compile on JDK 21
(`NoSuchFieldError: JCTree$JCImport.qualid`). `tools/install-jmeter.sh` builds
the plugin from its `v1.2.5.1` tag with `-Dlombok.version=1.18.34`.

Only the Lombok version is overridden. No plugin source is patched, and the
resulting jar's sha256 is recorded in every run manifest.

## ghz

| Setting | Value | Rationale |
|---|---|---|
| `--connections` | swept: `= concurrency`, then smaller | the matched setting reproduces the plugin's topology; the rest isolates multiplexing |
| `--disable-template-data`, `--disable-template-functions` | on | keeps per-request work static so the comparison is not about templating |
| `--duration-stop` | `wait` | in-flight requests are allowed to finish rather than being counted as failures |
| `--count-errors` | on | errors appear in the record instead of quietly vanishing from the stats |
| `--timeout` | `REQUEST_TIMEOUT_MS` | matches JMeter's deadline exactly |
| `--format json` | on | emits the per-request `details` array the harness normalizes |
| `--cpus` | left at default (all cores) | JMeter is not restricted either; restricting one side only would be the handicap |

Not applied, and why:

- **`--async`.** It would make ghz open-loop, which is not comparable with
  JMeter threads. It belongs to the open-loop family, where it is used
  deliberately.
- **`--skipFirst`.** Skips a fixed count rather than a fixed duration, so the
  faster tool would get a longer real warmup. The harness cuts by time instead.
- **`--protoset`.** Pre-compiling the descriptor would remove startup parsing
  from ghz while the plugin still pays it. Both parse the same `.proto` at
  startup, and startup is excluded from the window for both.

## Operating system

Applied to the load-generator host, identically for both tools:

| Setting | Note |
|---|---|
| `ulimit -n` | must exceed the highest thread count in the matrix; the manifest records it |
| `net.core.somaxconn` | recorded in the manifest; raise it if connection setup shows up as errors at high concurrency |
| CPU pinning | `SUT_CPUS` / `LOADGEN_CPUS` for single-host runs only; unset for the two-machine setup |
