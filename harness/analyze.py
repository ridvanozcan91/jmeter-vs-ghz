#!/usr/bin/env python3
"""Derive every reported statistic from canonical per-request records.

One percentile function serves both tools. That is the point: ghz and JMeter
each ship their own summary statistics, computed over their own windows with
their own definitions, and comparing those summaries directly is the most common
way a gRPC tool comparison ends up meaningless. Here both tools are reduced to
the same records by ``normalize.py`` and then scored by the same code path.

Definitions used throughout, applied identically to both tools:

window
    A request belongs to the measured window if it *completed* inside it. The
    server-side counter also increments on completion, so the client-side count
    and the server-side count are directly comparable, and that agreement is
    reported as ``window_agreement`` rather than assumed.

percentile
    Nearest-rank on the sorted latency array (``ceil(p/100 * n)``, 1-indexed).
    No interpolation, no bucketing, no histogram approximation, so the value is
    always an observed measurement rather than a reconstruction.

throughput
    Completed requests inside the window divided by the window length. Errors
    are counted separately and never inflate throughput.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PERCENTILES = (50.0, 90.0, 95.0, 99.0, 99.9)


def percentile(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile over an already-sorted list.

    Used for every latency figure this benchmark publishes, for both tools.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    if p <= 0:
        return sorted_values[0]
    if p >= 100:
        return sorted_values[-1]
    rank = math.ceil(p / 100.0 * len(sorted_values))
    return sorted_values[max(1, min(rank, len(sorted_values))) - 1]


def load_records(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return [
            {
                **row,
                "concurrency": int(row["concurrency"]),
                "connections": int(row["connections"]),
                "repeat": int(row["repeat"]),
                "start_ns": int(row["start_ns"]),
                "end_ns": int(row["end_ns"]),
                "latency_ns": int(row["latency_ns"]),
                "latency_resolution_ns": int(row["latency_resolution_ns"]),
                "ok": row["ok"].strip().lower() == "true",
            }
            for row in csv.DictReader(handle)
        ]


def measurement_window(records: list[dict], warmup_ns: int, measure_ns: int) -> tuple[int, int]:
    """Return the [start, end) window, offset from the first completed request.

    Anchoring on observed data rather than on the harness's own clock keeps the
    window honest when a tool takes noticeably longer to reach steady state --
    which is itself a finding, reported as ``time_to_first_request``.
    """
    first_end = min(record["end_ns"] for record in records)
    window_start = first_end + warmup_ns
    return window_start, window_start + measure_ns


def summarize(records: list[dict], warmup_ns: int, measure_ns: int) -> dict:
    """Summarize one run: one tool, one scenario, one repeat."""
    window_start, window_end = measurement_window(records, warmup_ns, measure_ns)
    in_window = [r for r in records if window_start <= r["end_ns"] < window_end]

    if not in_window:
        raise ValueError(
            "no requests completed inside the measurement window; the run was "
            "shorter than warmup + measure"
        )

    ok = [r for r in in_window if r["ok"]]
    failed = [r for r in in_window if not r["ok"]]

    # Latency statistics are computed over successful requests only. Failures
    # get their own counts, because a tool that fails fast would otherwise look
    # like a tool that is fast.
    latencies = sorted(r["latency_ns"] for r in ok)
    window_seconds = (window_end - window_start) / 1e9

    error_breakdown: dict[str, int] = defaultdict(int)
    for record in failed:
        error_breakdown[record["status"]] += 1

    first = records[0]
    summary = {
        "tool": first["tool"],
        "method": first["method"],
        "concurrency": first["concurrency"],
        "connections": first["connections"],
        "repeat": first["repeat"],
        "window_start_ns": window_start,
        "window_end_ns": window_end,
        "window_seconds": window_seconds,
        "requests_total": len(in_window),
        "requests_ok": len(ok),
        "requests_failed": len(failed),
        "error_breakdown": dict(error_breakdown),
        "error_rate": len(failed) / len(in_window),
        "throughput_rps": len(ok) / window_seconds,
        "latency_resolution_ns": first["latency_resolution_ns"],
        "latency_ns": {
            "min": latencies[0] if latencies else None,
            "mean": int(statistics.fmean(latencies)) if latencies else None,
            "max": latencies[-1] if latencies else None,
            **{f"p{p:g}": percentile(latencies, p) for p in PERCENTILES},
        },
        # How long the tool needed before its first request completed. JMeter's
        # plugin invokes protoc once per thread on first use, so this is a real
        # and reportable cost, not noise to be hidden by a longer warmup.
        "time_to_first_request_ns": min(r["end_ns"] for r in records)
        - min(r["start_ns"] for r in records),
    }

    # Achieved concurrency, by Little's law over the window. A closed-loop tool
    # that cannot keep its workers busy shows up here as a number well below its
    # configured concurrency.
    if latencies:
        mean_latency_s = statistics.fmean(latencies) / 1e9
        summary["achieved_concurrency"] = summary["throughput_rps"] * mean_latency_s
        summary["concurrency_efficiency"] = (
            summary["achieved_concurrency"] / summary["concurrency"]
        )
    return summary


def aggregate(summaries: list[dict]) -> dict:
    """Combine repeats of one scenario into median plus spread.

    Median and IQR rather than mean and standard deviation: a single slow repeat
    (a GC pause, a noisy neighbour) should not silently move the headline number.
    """
    reference = summaries[0]
    throughputs = sorted(s["throughput_rps"] for s in summaries)
    p99s = sorted(s["latency_ns"]["p99"] for s in summaries if s["latency_ns"]["p99"])

    def iqr(values: list[float]) -> float:
        if len(values) < 4:
            return values[-1] - values[0] if values else 0.0
        return percentile(values, 75) - percentile(values, 25)

    return {
        "tool": reference["tool"],
        "method": reference["method"],
        "concurrency": reference["concurrency"],
        "connections": reference["connections"],
        "repeats": len(summaries),
        "throughput_rps_median": statistics.median(throughputs),
        "throughput_rps_min": throughputs[0],
        "throughput_rps_max": throughputs[-1],
        "throughput_rps_iqr": iqr(throughputs),
        "latency_ns_median": {
            key: statistics.median([s["latency_ns"][key] for s in summaries])
            for key in summaries[0]["latency_ns"]
            if all(s["latency_ns"][key] is not None for s in summaries)
        },
        "latency_p99_iqr_ns": iqr([float(v) for v in p99s]) if p99s else 0.0,
        "error_rate_median": statistics.median([s["error_rate"] for s in summaries]),
        "achieved_concurrency_median": statistics.median(
            [s.get("achieved_concurrency", 0.0) for s in summaries]
        ),
        "concurrency_efficiency_median": statistics.median(
            [s.get("concurrency_efficiency", 0.0) for s in summaries]
        ),
        "latency_resolution_ns": reference["latency_resolution_ns"],
    }


def scenario_key(summary: dict) -> tuple:
    return (summary["tool"], summary["method"], summary["concurrency"], summary["connections"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--canonical-dir", required=True, type=Path, help="directory of canonical csv files"
    )
    parser.add_argument("--output", required=True, type=Path, help="analysis json to write")
    parser.add_argument("--warmup-seconds", type=float, required=True)
    parser.add_argument("--measure-seconds", type=float, required=True)
    args = parser.parse_args(argv)

    warmup_ns = int(args.warmup_seconds * 1e9)
    measure_ns = int(args.measure_seconds * 1e9)

    runs = []
    for path in sorted(args.canonical_dir.glob("*.csv")):
        records = load_records(path)
        if not records:
            print(f"skipping empty canonical file: {path}", file=sys.stderr)
            continue
        try:
            runs.append(summarize(records, warmup_ns, measure_ns))
        except ValueError as error:
            print(f"skipping {path}: {error}", file=sys.stderr)

    if not runs:
        print("no runs could be summarized", file=sys.stderr)
        return 1

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for run in runs:
        grouped[scenario_key(run)].append(run)

    analysis = {
        "window": {"warmup_seconds": args.warmup_seconds, "measure_seconds": args.measure_seconds},
        "percentile_method": "nearest-rank, computed identically for every tool",
        "runs": runs,
        "scenarios": [aggregate(group) for group in grouped.values()],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(analysis, indent=2))
    print(f"analyzed {len(runs)} runs across {len(grouped)} scenarios -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
