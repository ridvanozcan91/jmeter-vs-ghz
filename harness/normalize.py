#!/usr/bin/env python3
"""Reduce raw load-generator output to one canonical per-request table.

This module is where the benchmark earns the right to call itself fair.

Neither tool's summary is ever compared against the other's. ghz reports its own
percentiles, JMeter reports its own, and the two are computed over different
windows with different definitions. Instead, both tools' raw per-request records
are reduced here to identical columns, and every statistic downstream is derived
from those columns by a single function in ``analyze.py``.

Two asymmetries are corrected here, and one is deliberately left uncorrected:

corrected -- timestamp semantics
    ghz records ``timestamp`` as the RPC *end* time (``stats_handler.go`` passes
    ``rs.EndTime``). JMeter's ``timeStamp`` is the sample *start* time when
    ``sampleresult.timestamp.start=true``, which the runner sets explicitly so
    the semantics cannot drift with a properties file. Both are converted to an
    explicit ``start_ns`` and ``end_ns`` pair.

corrected -- status vocabulary
    ghz reports gRPC status names ("OK", "DeadlineExceeded"). The JMeter plugin
    reports an HTTP-flavoured response code plus a success flag. Both are mapped
    onto a single ``ok`` boolean plus a raw status string kept for the record.

NOT corrected -- timer resolution
    JMeter's ``elapsed`` is integer milliseconds; ghz's latency is nanoseconds.
    Rounding JMeter's values or truncating ghz's would invent precision that one
    tool does not have. The difference is reported as a caveat instead
    (``docs/METHODOLOGY.md``), and scenarios whose latency is dominated by
    sub-millisecond values are read for throughput rather than latency shape.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

CANONICAL_FIELDS = [
    "tool",
    "method",
    "concurrency",
    "connections",
    "target_rps",
    "repeat",
    "start_ns",
    "end_ns",
    "latency_ns",
    "status",
    "ok",
    "latency_resolution_ns",
]

# Resolution of each tool's latency measurement, carried through so that the
# analysis can flag comparisons the underlying data cannot support.
JMETER_RESOLUTION_NS = 1_000_000
GHZ_RESOLUTION_NS = 1


@dataclass(frozen=True)
class RunMeta:
    """Scenario coordinates attached to every record of a single run."""

    tool: str
    method: str
    concurrency: int
    connections: int
    # 0 means closed loop: throughput is an outcome, not a target. Non-zero puts
    # the run in the open-loop family, which is never tabulated alongside it.
    target_rps: int
    repeat: int


def parse_rfc3339_ns(value: str) -> int:
    """Parse ghz's RFC3339 timestamp into Unix nanoseconds without losing digits.

    ``datetime.fromisoformat`` truncates to microseconds, which would silently
    discard the last three digits of a nanosecond timestamp. The sub-second part
    is therefore parsed as text and rescaled.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    # Split off the fractional seconds, which sit between "." and the offset.
    dot = text.find(".")
    if dot == -1:
        moment = datetime.fromisoformat(text)
        return int(moment.timestamp()) * 1_000_000_000

    offset_pos = len(text)
    for index in range(dot, len(text)):
        if text[index] in "+-":
            offset_pos = index
            break

    fraction = text[dot + 1 : offset_pos]
    whole = text[:dot] + text[offset_pos:]
    moment = datetime.fromisoformat(whole)
    fraction_ns = int((fraction + "000000000")[:9])
    return int(moment.timestamp()) * 1_000_000_000 + fraction_ns


def normalize_ghz(path: Path, meta: RunMeta) -> list[dict]:
    """Convert a ghz ``--format json`` report into canonical records."""
    with path.open() as handle:
        report = json.load(handle)

    details = report.get("details") or []
    if not details:
        raise ValueError(f"{path}: ghz report contains no per-request details")

    records = []
    for detail in details:
        end_ns = parse_rfc3339_ns(detail["timestamp"])
        latency_ns = int(detail["latency"])
        status = detail.get("status") or "Unknown"
        records.append(
            {
                **asdict(meta),
                "start_ns": end_ns - latency_ns,
                "end_ns": end_ns,
                "latency_ns": latency_ns,
                "status": status,
                "ok": status == "OK" and not detail.get("error"),
                "latency_resolution_ns": GHZ_RESOLUTION_NS,
            }
        )
    return records


def normalize_jmeter(path: Path, meta: RunMeta) -> list[dict]:
    """Convert a JMeter CSV result file into canonical records."""
    records = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timeStamp", "elapsed", "success"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: JMeter CSV is missing columns {sorted(missing)}")

        for row in reader:
            start_ns = int(row["timeStamp"]) * 1_000_000
            latency_ns = int(row["elapsed"]) * 1_000_000
            success = row["success"].strip().lower() == "true"
            records.append(
                {
                    **asdict(meta),
                    "start_ns": start_ns,
                    "end_ns": start_ns + latency_ns,
                    "latency_ns": latency_ns,
                    # The plugin maps every outcome onto an HTTP-like code; the
                    # response code is kept verbatim so failures stay auditable.
                    "status": "OK" if success else (row.get("responseCode") or "ERROR"),
                    "ok": success,
                    "latency_resolution_ns": JMETER_RESOLUTION_NS,
                }
            )

    if not records:
        raise ValueError(f"{path}: JMeter CSV contains no samples")
    return records


def normalize(path: Path, meta: RunMeta) -> list[dict]:
    if meta.tool.startswith("ghz"):
        return normalize_ghz(path, meta)
    if meta.tool.startswith("jmeter"):
        return normalize_jmeter(path, meta)
    raise ValueError(f"unknown tool: {meta.tool}")


def write_canonical(records: list[dict], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_FIELDS)
        writer.writeheader()
        writer.writerows(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path, help="raw ghz.json or jmeter.csv")
    parser.add_argument("--output", required=True, type=Path, help="canonical csv to write")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--concurrency", required=True, type=int)
    parser.add_argument("--connections", required=True, type=int)
    parser.add_argument(
        "--target-rps", type=int, default=0, help="0 for closed-loop runs"
    )
    parser.add_argument("--repeat", required=True, type=int)
    args = parser.parse_args(argv)

    meta = RunMeta(
        tool=args.tool,
        method=args.method,
        concurrency=args.concurrency,
        connections=args.connections,
        target_rps=args.target_rps,
        repeat=args.repeat,
    )
    records = normalize(args.input, meta)
    write_canonical(records, args.output)
    print(f"{args.input} -> {args.output} ({len(records)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
