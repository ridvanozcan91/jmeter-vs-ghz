#!/usr/bin/env python3
"""Capture the server-side referee metrics around a run.

The benchmark never takes a load generator's word for what happened. This
scrapes the SUT's Prometheus endpoint before and after a run and reports the
deltas, which give three things no client-side measurement can:

  * an independent request count, used to check that the client's measurement
    window and the server's view of the same seconds agree;
  * server-side handling latency, so client-reported latency can be decomposed
    into "time the server took" versus "overhead the tool added";
  * the number of connections the tool is holding open, which is how the HTTP/2
    multiplexing difference between the two tools stops being an assertion and
    becomes a measurement.

Connection and in-flight counts are gauges: they fall back to zero the moment a
run ends, so a before/after pair cannot see them. The ``series`` mode polls the
endpoint throughout a run and keeps the peaks, which is the only way those two
numbers survive to the report.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Metrics the report depends on. Anything else in the endpoint is ignored so a
# snapshot stays small enough to commit alongside results.
WANTED_PREFIXES = (
    "benchmark_server_",
    "jvm_gc_pause_seconds",
    "jvm_memory_used_bytes",
    "process_cpu_usage",
    "system_cpu_usage",
    "system_load_average_1m",
    "executor_",
)


def parse_prometheus(text: str) -> dict[str, float]:
    """Parse the Prometheus text exposition format into a flat dict.

    Keys keep their label set verbatim so that per-method and per-status series
    stay distinguishable.
    """
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(WANTED_PREFIXES):
            continue
        # A sample line is "name{labels} value" or "name value".
        split_at = line.rfind(" ")
        if split_at == -1:
            continue
        key, raw_value = line[:split_at], line[split_at + 1 :]
        try:
            metrics[key] = float(raw_value)
        except ValueError:
            continue
    return metrics


def scrape(url: str, timeout: float = 10.0) -> dict[str, float]:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return parse_prometheus(response.read().decode("utf-8"))


def delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Counter deltas, plus gauges taken at their 'after' value.

    Counters are identified by the Prometheus convention of a ``_total`` suffix
    (and Micrometer's ``_count``/``_sum`` timer series), because only those are
    meaningful as a difference.
    """
    result: dict[str, float] = {}
    for key, after_value in after.items():
        is_cumulative = any(
            marker in key for marker in ("_total", "_count", "_sum", "_seconds_max")
        )
        if is_cumulative and key in before:
            result[key] = after_value - before[key]
        elif not is_cumulative:
            result[key] = after_value
    return result


def sample_series(url: str, interval: float, output: Path) -> int:
    """Poll the endpoint until signalled, then write the series and its peaks."""
    samples: list[dict] = []
    stop = False

    def handle_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    while not stop:
        try:
            samples.append({"unix_ns": time.time_ns(), "metrics": scrape(url, timeout=2.0)})
        except (urllib.error.URLError, OSError):
            # A scrape can fail while the server is saturated. Losing a sample is
            # acceptable; aborting the series would lose the whole run's peaks.
            pass
        time.sleep(interval)

    payload = {"url": url, "mode": "series", "samples": samples, "peaks": peaks(samples)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2))
    return 0


def peaks(samples: list[dict]) -> dict[str, float]:
    """Maximum observed value of each gauge across the run.

    Peak rather than final value: connections and in-flight RPCs both collapse
    to zero as the run drains, and the peak is what says how much concurrency
    and how many connections the tool actually held.
    """
    result: dict[str, float] = {}
    for sample in samples:
        for key, value in sample["metrics"].items():
            result[key] = max(result.get(key, value), value)
    return result


def requests_in_window(samples: list[dict], start_ns: int, end_ns: int, metric_prefix: str) -> float | None:
    """Server-counted completions between two instants.

    This is the independent check on the client's measurement window: both sides
    count completions, so the two totals should agree closely. They are computed
    from the samples bracketing the window rather than from the run as a whole.
    """
    inside = [s for s in samples if start_ns <= s["unix_ns"] <= end_ns]
    if len(inside) < 2:
        return None

    def total(sample: dict) -> float:
        return sum(v for k, v in sample["metrics"].items() if k.startswith(metric_prefix))

    return total(inside[-1]) - total(inside[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--phase", required=True, choices=("before", "after", "series"))
    parser.add_argument("--interval", type=float, default=0.5, help="series mode poll interval")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--before-file",
        type=Path,
        help="when phase=after, the matching before snapshot, to emit deltas",
    )
    args = parser.parse_args(argv)

    if args.phase == "series":
        return sample_series(args.url, args.interval, args.output)

    try:
        metrics = scrape(args.url)
    except (urllib.error.URLError, OSError) as error:
        # A missing referee is a serious problem, but it must not silently
        # produce an empty snapshot that later reads as "zero requests".
        print(f"failed to scrape {args.url}: {error}", file=sys.stderr)
        return 1

    payload: dict = {"url": args.url, "phase": args.phase, "metrics": metrics}

    if args.phase == "after" and args.before_file and args.before_file.exists():
        before = json.loads(args.before_file.read_text())["metrics"]
        payload["delta"] = delta(before, metrics)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    print(f"scraped {len(metrics)} series -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
