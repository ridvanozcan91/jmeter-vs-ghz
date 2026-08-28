#!/usr/bin/env python3
"""Sample load-generator resource usage from /proc, with no dependencies.

The headline metric of this benchmark is not raw throughput, it is throughput
per unit of load-generator CPU. A tool that reaches 5k RPS while pinning three
cores has not beaten a tool that reaches 4k RPS on a third of one core; it has
merely been given more hardware. Without this sampler the comparison cannot
distinguish "the server was saturated" from "the load generator was saturated",
which is the whole question being asked.

``pidstat`` is not assumed to be installed. Everything here reads /proc
directly, so the sampler runs anywhere the benchmark does.

The whole process tree is summed, not just the root pid. Both load generators
are launched through wrapper scripts, so the root pid is a shell that burns no
CPU while its child does all the work; sampling only the root would report every
run as costing nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def descendants(pid: int) -> list[int]:
    """Every pid in the tree rooted at ``pid``, including ``pid`` itself.

    Read from /proc rather than by shelling out to pgrep, so the sampler keeps
    its no-dependency promise.
    """
    children: dict[int, list[int]] = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            stat_text = Path(entry.path, "stat").read_text()
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
        close_paren = stat_text.rfind(")")
        fields = stat_text[close_paren + 2 :].split()
        parent = int(fields[1])
        children.setdefault(parent, []).append(int(entry.name))

    tree, queue = [], [pid]
    while queue:
        current = queue.pop()
        tree.append(current)
        queue.extend(children.get(current, []))
    return tree


def read_process_stat(pid: int) -> dict | None:
    """Cumulative CPU, memory and thread count for one process.

    Returns None once the process is gone, which is the normal way a sample loop
    ends when the load generator exits on its own.
    """
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        status_text = Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None

    # The comm field can contain spaces and parentheses, so split after it.
    close_paren = stat_text.rfind(")")
    fields = stat_text[close_paren + 2 :].split()

    # Fields are 1-indexed from 'state' in proc(5); utime is 14, stime is 15,
    # which land at offsets 11 and 12 after the comm field is removed.
    utime_ticks = int(fields[11])
    stime_ticks = int(fields[12])
    num_threads = int(fields[17])

    rss_kb = 0
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            rss_kb = int(line.split()[1])
            break

    return {
        "cpu_seconds": (utime_ticks + stime_ticks) / CLOCK_TICKS,
        "threads": num_threads,
        "rss_bytes": rss_kb * 1024,
    }


def read_cgroup_cpu_limit() -> float | None:
    """The container's CPU limit in cores, or None when it is unlimited.

    In a container ``nproc`` reports the node's cores, not the pod's budget, so
    it cannot say whether the load generator had room left. The cgroup does.
    Both hierarchies are read because a run may land on either.
    """
    v2 = Path("/sys/fs/cgroup/cpu.max")
    if v2.exists():
        try:
            quota, period = v2.read_text().split()
        except (OSError, ValueError):
            return None
        if quota == "max":
            return None
        return int(quota) / int(period)

    quota_file = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period_file = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota_file.exists() and period_file.exists():
        try:
            quota = int(quota_file.read_text().strip())
            period = int(period_file.read_text().strip())
        except (OSError, ValueError):
            return None
        if quota <= 0 or period <= 0:
            return None
        return quota / period
    return None


def read_cgroup_cpu_stat() -> dict | None:
    """Cumulative CFS throttling counters, or None outside a limited cgroup.

    A throttled load generator looks identical to a saturated one in /proc: both
    report their CPU pegged at a ceiling. Only these counters distinguish "the
    tool ran out of work it could do" from "the scheduler took the core away",
    and confusing the two turns a quota setting into a finding about a tool.
    """
    for path, scale in (
        (Path("/sys/fs/cgroup/cpu.stat"), 1e-6),          # v2: throttled_usec
        (Path("/sys/fs/cgroup/cpu/cpu.stat"), 1e-9),      # v1: throttled_time (ns)
    ):
        if not path.exists():
            continue
        values = {}
        try:
            for line in path.read_text().splitlines():
                key, _, value = line.partition(" ")
                values[key] = int(value)
        except (OSError, ValueError):
            return None
        if "nr_periods" not in values:
            continue
        throttled = values.get("throttled_usec", values.get("throttled_time", 0))
        return {
            "nr_periods": values["nr_periods"],
            "nr_throttled": values.get("nr_throttled", 0),
            "throttled_seconds": throttled * scale,
        }
    return None


def read_system_cpu_seconds() -> float:
    """Total busy CPU seconds across all cores, for context on the sample."""
    fields = Path("/proc/stat").read_text().split("\n")[0].split()[1:]
    values = [int(v) for v in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return (sum(values) - idle) / CLOCK_TICKS


def open_socket_count(pid: int) -> int:
    try:
        return sum(
            1
            for entry in os.scandir(f"/proc/{pid}/fd")
            if (os.readlink(entry.path).startswith("socket:") if entry.is_symlink() else False)
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return 0


def sample(pid: int, interval: float, output: Path) -> int:
    samples: list[dict] = []
    stop = False

    def handle_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    while not stop:
        tree = descendants(pid)
        stats = [stat for stat in (read_process_stat(child) for child in tree) if stat]
        if not stats:
            break

        # CPU is cumulative per process, so a child that exits mid-run would make
        # the running total go backwards. Tracking the peak keeps the series
        # monotonic, which is what the end-minus-start delta assumes.
        total_cpu = sum(stat["cpu_seconds"] for stat in stats)
        peak_cpu = max(total_cpu, samples[-1]["cpu_seconds"] if samples else 0.0)

        samples.append(
            {
                "unix_ns": time.time_ns(),
                "monotonic": time.monotonic(),
                "cpu_seconds": peak_cpu,
                "threads": sum(stat["threads"] for stat in stats),
                "rss_bytes": sum(stat["rss_bytes"] for stat in stats),
                "sockets": sum(open_socket_count(child) for child in tree),
                "process_count": len(stats),
                "system_cpu_seconds": read_system_cpu_seconds(),
                "cgroup_cpu_stat": read_cgroup_cpu_stat(),
            }
        )
        time.sleep(interval)

    summary = summarize(samples)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"pid": pid, "summary": summary, "samples": samples}, indent=2))
    return 0


def summarize(samples: list[dict]) -> dict:
    """Reduce the sample series to the figures the report actually uses."""
    if len(samples) < 2:
        return {"usable": False, "reason": "fewer than two samples"}

    first, last = samples[0], samples[-1]
    wall_seconds = last["monotonic"] - first["monotonic"]
    if wall_seconds <= 0:
        return {"usable": False, "reason": "zero-length sampling window"}

    cpu_seconds = last["cpu_seconds"] - first["cpu_seconds"]
    summary = {
        "usable": True,
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        # 1.0 means one core fully busy. Divided into throughput downstream to
        # give requests per second per core.
        "cpu_cores_used": cpu_seconds / wall_seconds,
        "rss_bytes_max": max(s["rss_bytes"] for s in samples),
        "threads_max": max(s["threads"] for s in samples),
        "sockets_max": max(s["sockets"] for s in samples),
        "process_count_max": max(s.get("process_count", 1) for s in samples),
        "sample_count": len(samples),
    }
    summary.update(summarize_cgroup(samples, wall_seconds, cpu_seconds))
    return summary


def summarize_cgroup(samples: list[dict], wall_seconds: float, cpu_seconds: float) -> dict:
    """Reduce the cgroup counters to what makes a container run interpretable.

    Outside a container every field here is None, and the report says nothing
    about limits it did not find. Inside one, ``cpu_utilization_of_limit`` near
    1.0 with a non-zero throttled fraction means the ceiling reported for that
    tool is the quota's, not the tool's.
    """
    limit = read_cgroup_cpu_limit()
    result = {
        "cpu_limit_cores": limit,
        "cpu_utilization_of_limit": (cpu_seconds / wall_seconds / limit) if limit else None,
        "throttled_periods_fraction": None,
        "throttled_seconds": None,
    }

    first_stat = samples[0].get("cgroup_cpu_stat")
    last_stat = samples[-1].get("cgroup_cpu_stat")
    if not first_stat or not last_stat:
        return result

    periods = last_stat["nr_periods"] - first_stat["nr_periods"]
    throttled = last_stat["nr_throttled"] - first_stat["nr_throttled"]
    if periods > 0:
        result["throttled_periods_fraction"] = throttled / periods
    result["throttled_seconds"] = (
        last_stat["throttled_seconds"] - first_stat["throttled_seconds"]
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    return sample(args.pid, args.interval, args.output)


if __name__ == "__main__":
    sys.exit(main())
