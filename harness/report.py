#!/usr/bin/env python3
"""Render a run into a Markdown report with dependency-free SVG charts.

Charts are emitted as hand-written SVG rather than through a plotting library so
that reproducing this benchmark needs nothing beyond a Python interpreter. A
harness that is hard to install is a harness whose numbers nobody checks.

The report deliberately leads with load-generator cost rather than raw
throughput. "Which tool produced more requests per second" is the wrong question
on its own: it is only meaningful alongside what each tool spent to do it, and
whether the server or the client was the thing that ran out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Colours are picked to stay legible on both light and dark backgrounds, since
# the report is read on GitHub in either theme.
COLOR_JMETER = "#d1495b"
COLOR_GHZ = "#2a9d8f"
COLOR_GHZ_ALT = "#457b9d"
COLOR_AXIS = "#8a8f98"
COLOR_TEXT = "#8a8f98"


def series_color(tool: str, connections: int, concurrency: int) -> str:
    if tool.startswith("jmeter"):
        return COLOR_JMETER
    return COLOR_GHZ if connections == concurrency else COLOR_GHZ_ALT


def series_label(tool: str, connections: int, concurrency: int) -> str:
    """Name a series by what makes it different, not by its raw parameters."""
    if tool.startswith("jmeter"):
        return "JMeter (plugin, 1 channel/thread)"
    if connections == concurrency:
        return "ghz (connections = concurrency)"
    return f"ghz ({connections} connection{'s' if connections != 1 else ''}, multiplexed)"


def load_client_resources(results_dir: Path) -> dict[str, dict]:
    """Map a run tag to its client resource summary, when one was captured."""
    resources = {}
    for path in (results_dir / "raw").glob("*/client-resources.json"):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        summary = payload.get("summary", {})
        if summary.get("usable"):
            resources[path.parent.name] = summary
    return resources


def load_server_series(results_dir: Path) -> dict[str, dict]:
    """Map a run tag to the metrics series sampled while that run was happening.

    The series, not the before/after pair, is what carries connection counts and
    in-flight concurrency: both are gauges that return to zero once a run ends.
    """
    server = {}
    for path in (results_dir / "raw").glob("*/server-series.json"):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if payload.get("samples"):
            server[path.parent.name] = payload
    return server


def run_tag(run: dict) -> str:
    return (
        f"{run['tool']}_{run['method']}_c{run['concurrency']}"
        f"_conn{run['connections']}_r{run['repeat']}"
    )


def server_completions_in_window(payload: dict, method: str, start_ns: int, end_ns: int):
    """Completions the server counted over exactly the client's window.

    Comparing this with the client's own count is the check that the two sides
    are describing the same seconds. A large gap means the window is wrong, and
    the run should be re-read rather than reported.
    """
    suffix = {"echo": "Echo", "compute": "Compute", "payload": "Payload"}.get(method)
    if not suffix:
        return None

    def total(sample: dict) -> float:
        return sum(
            value
            for key, value in sample["metrics"].items()
            if key.startswith("benchmark_server_rpcs_completed_total") and f"/{suffix}" in key
        )

    inside = [s for s in payload["samples"] if start_ns <= s["unix_ns"] <= end_ns]
    if len(inside) < 2:
        return None
    return total(inside[-1]) - total(inside[0])


def server_peak(payload: dict, metric: str) -> float | None:
    for key, value in payload.get("peaks", {}).items():
        if key.startswith(metric):
            return value
    return None


def fmt_ms(nanos: float | None) -> str:
    return "n/a" if nanos is None else f"{nanos / 1e6:.2f}"


def svg_bar_chart(
    title: str,
    groups: list[str],
    series: list[tuple[str, str, list[float]]],
    y_label: str,
) -> str:
    """Grouped bar chart. ``series`` is (label, colour, values-per-group)."""
    if not groups or not series:
        return ""

    width, height = 760, 340
    left, right, top, bottom = 70, 20, 54, 76
    plot_w = width - left - right
    plot_h = height - top - bottom

    peak = max((max(values) for _, _, values in series if values), default=0.0)
    if peak <= 0:
        return ""
    # Round the axis up to something readable rather than to the exact peak.
    magnitude = 10 ** max(0, len(str(int(peak))) - 2)
    y_max = (int(peak / magnitude) + 1) * magnitude

    group_w = plot_w / len(groups)
    bar_w = group_w / (len(series) + 1)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{title}" style="max-width:100%;height:auto">',
        f'<text x="{left}" y="22" font-family="sans-serif" font-size="14" '
        f'font-weight="600" fill="{COLOR_TEXT}">{title}</text>',
    ]

    # Horizontal gridlines and y-axis labels.
    for step in range(5):
        value = y_max * step / 4
        y = top + plot_h - (plot_h * step / 4)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            f'stroke="{COLOR_AXIS}" stroke-opacity="0.25" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-family="sans-serif" '
            f'font-size="10" fill="{COLOR_TEXT}">{value:,.0f}</text>'
        )

    parts.append(
        f'<text x="14" y="{top + plot_h / 2:.0f}" font-family="sans-serif" font-size="11" '
        f'fill="{COLOR_TEXT}" transform="rotate(-90 14 {top + plot_h / 2:.0f})" '
        f'text-anchor="middle">{y_label}</text>'
    )

    for group_index, group in enumerate(groups):
        group_x = left + group_index * group_w
        for series_index, (_, color, values) in enumerate(series):
            if group_index >= len(values):
                continue
            value = values[group_index]
            bar_h = plot_h * (value / y_max) if y_max else 0
            x = group_x + bar_w * (series_index + 0.5)
            y = top + plot_h - bar_h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w * 0.86:.1f}" '
                f'height="{max(bar_h, 0.5):.1f}" fill="{color}" rx="2"/>'
            )
        parts.append(
            f'<text x="{group_x + group_w / 2:.1f}" y="{top + plot_h + 18}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="11" '
            f'fill="{COLOR_TEXT}">{group}</text>'
        )

    # Legend below the plot, one entry per series.
    legend_y = height - 32
    legend_x = left
    for label, color, _ in series:
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 9}" width="11" height="11" fill="{color}" rx="2"/>'
        )
        parts.append(
            f'<text x="{legend_x + 17}" y="{legend_y}" font-family="sans-serif" font-size="11" '
            f'fill="{COLOR_TEXT}">{label}</text>'
        )
        legend_x += 20 + len(label) * 6.2

    parts.append("</svg>")
    return "\n".join(parts)


def build_report(results_dir: Path) -> str:
    analysis = json.loads((results_dir / "analysis.json").read_text())
    manifest_path = results_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    resources = load_client_resources(results_dir)
    server_metrics = load_server_series(results_dir)

    lines: list[str] = []
    lines.append("# Benchmark report")
    lines.append("")

    host = manifest.get("host", {})
    target = manifest.get("target", {})
    tools = manifest.get("tools", {})

    if target.get("same_host_as_loadgen"):
        lines.append(
            "> **These numbers are directional, not authoritative.** The load "
            "generator and the server shared a host, so they competed for the "
            "same CPUs. Treat the ordering as meaningful and the magnitudes as "
            "not. See `docs/RUNBOOK.md` for the two-machine procedure that "
            "produces publishable figures."
        )
        lines.append("")

    lines.append("## Environment")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| Run id | `{manifest.get('run_id', 'unknown')}` |")
    lines.append(f"| Captured | {manifest.get('captured_at_utc', 'unknown')} |")
    lines.append(f"| Commit | `{manifest.get('git', {}).get('commit', 'unknown')}` |")
    lines.append(f"| CPU | {host.get('cpu_model', 'unknown')} ({host.get('cpu_count', '?')} cores) |")
    lines.append(f"| Kernel | {host.get('kernel', 'unknown')} |")
    lines.append(f"| Java | {tools.get('java', 'unknown')} |")
    lines.append(f"| ghz | {tools.get('ghz', 'unknown')} |")
    lines.append(
        f"| JMeter plugin sha256 | `{tools.get('jmeter_plugin_sha256', 'unknown')[:16]}…` |"
    )
    lines.append(
        f"| Window | {analysis['window']['warmup_seconds']:g}s warmup, "
        f"{analysis['window']['measure_seconds']:g}s measured |"
    )
    lines.append(f"| Percentiles | {analysis['percentile_method']} |")
    lines.append("")

    scenarios = analysis["scenarios"]
    methods = sorted({s["method"] for s in scenarios})

    for method in methods:
        method_scenarios = [s for s in scenarios if s["method"] == method]
        concurrencies = sorted({s["concurrency"] for s in method_scenarios})

        lines.append(f"## Method: `{method}`")
        lines.append("")

        # --- Throughput chart ------------------------------------------------
        variants: dict[tuple[str, str], list[float]] = {}
        for scenario in method_scenarios:
            label = series_label(
                scenario["tool"], scenario["connections"], scenario["concurrency"]
            )
            color = series_color(
                scenario["tool"], scenario["connections"], scenario["concurrency"]
            )
            variants.setdefault((label, color), [0.0] * len(concurrencies))
            index = concurrencies.index(scenario["concurrency"])
            variants[(label, color)][index] = scenario["throughput_rps_median"]

        chart_series = [(label, color, values) for (label, color), values in sorted(variants.items())]
        chart = svg_bar_chart(
            f"Throughput by concurrency — {method}",
            [str(c) for c in concurrencies],
            chart_series,
            "requests/sec (median)",
        )
        if chart:
            chart_path = results_dir / "charts" / f"throughput-{method}.svg"
            chart_path.parent.mkdir(parents=True, exist_ok=True)
            chart_path.write_text(chart)
            lines.append(f"![Throughput by concurrency for {method}](charts/throughput-{method}.svg)")
            lines.append("")

        # --- Detail table -----------------------------------------------------
        lines.append(
            "| Tool | Concurrency | Connections | RPS (median) | RPS spread (IQR) | "
            "p50 ms | p99 ms | Errors | Achieved concurrency | Client cores | RPS/core |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

        for scenario in sorted(
            method_scenarios, key=lambda s: (s["concurrency"], s["tool"], s["connections"])
        ):
            matching = [
                run
                for run in analysis["runs"]
                if run["tool"] == scenario["tool"]
                and run["method"] == method
                and run["concurrency"] == scenario["concurrency"]
                and run["connections"] == scenario["connections"]
            ]
            cores = [
                resources[run_tag(run)]["cpu_cores_used"]
                for run in matching
                if run_tag(run) in resources
            ]
            median_cores = sorted(cores)[len(cores) // 2] if cores else None
            rps = scenario["throughput_rps_median"]
            latency = scenario["latency_ns_median"]
            label = series_label(
                scenario["tool"], scenario["connections"], scenario["concurrency"]
            )
            cores_cell = f"{median_cores:.2f}" if median_cores else "n/a"
            per_core_cell = f"{rps / median_cores:,.0f}" if median_cores else "n/a"

            lines.append(
                f"| {label} | {scenario['concurrency']} | {scenario['connections']} "
                f"| {rps:,.0f} | {scenario['throughput_rps_iqr']:,.0f} "
                f"| {fmt_ms(latency.get('p50'))} | {fmt_ms(latency.get('p99'))} "
                f"| {scenario['error_rate_median'] * 100:.2f}% "
                f"| {scenario['achieved_concurrency_median']:.1f} "
                f"| {cores_cell} | {per_core_cell} |"
            )

        lines.append("")

    # --- Connection topology, the multiplexing evidence -----------------------
    connection_rows = []
    for run in analysis["runs"]:
        payload = server_metrics.get(run_tag(run))
        if not payload:
            continue
        peak_connections = server_peak(payload, "benchmark_server_connections_active")
        peak_in_flight = server_peak(payload, "benchmark_server_rpcs_in_flight")
        if peak_connections is None:
            continue
        connection_rows.append(
            (
                series_label(run["tool"], run["connections"], run["concurrency"]),
                run["method"],
                run["concurrency"],
                peak_connections,
                peak_in_flight,
            )
        )

    if connection_rows:
        lines.append("## Connections and in-flight RPCs, as counted by the server")
        lines.append("")
        lines.append(
            "The server counts its own transports and in-flight calls. This is "
            "what turns the HTTP/2 multiplexing difference from a claim into a "
            "measurement: the plugin opens one channel per JMeter thread, so its "
            "connection count tracks concurrency, while ghz spreads its workers "
            "over the connections it was given."
        )
        lines.append("")
        lines.append(
            "| Tool | Method | Configured concurrency | Peak connections | Peak in-flight RPCs |"
        )
        lines.append("|---|---|---:|---:|---:|")
        for label, method, concurrency, connections, in_flight in sorted(connection_rows):
            in_flight_cell = f"{in_flight:.0f}" if in_flight is not None else "n/a"
            lines.append(
                f"| {label} | `{method}` | {concurrency} | {connections:.0f} | {in_flight_cell} |"
            )
        lines.append("")

    # --- Client/server agreement ---------------------------------------------
    agreement_rows = []
    for run in analysis["runs"]:
        payload = server_metrics.get(run_tag(run))
        if not payload:
            continue
        server_count = server_completions_in_window(
            payload, run["method"], run["window_start_ns"], run["window_end_ns"]
        )
        if server_count is None:
            continue
        client_count = run["requests_total"]
        drift = (client_count - server_count) / server_count if server_count else 0.0
        agreement_rows.append((run_tag(run), client_count, server_count, drift))

    if agreement_rows:
        lines.append("## Client and server agreement")
        lines.append("")
        lines.append(
            "Both sides count completions, over the same window. They will not "
            "match exactly -- the server is polled twice a second, so its window "
            "edges are rounded to the nearest poll -- but a large gap means the "
            "window is wrong and the run should be re-read rather than reported."
        )
        lines.append("")
        lines.append("| Run | Client (window) | Server (same window) | Drift |")
        lines.append("|---|---:|---:|---:|")
        for tag, client_count, server_count, drift in sorted(agreement_rows):
            lines.append(
                f"| `{tag}` | {client_count:,} | {server_count:,.0f} | {drift * 100:+.1f}% |"
            )
        lines.append("")

    lines.append("## How to read this")
    lines.append("")
    lines.append(
        "- **RPS/core is the headline.** Raw throughput only says which tool was "
        "given more CPU; throughput per core of load generator says which tool "
        "uses what it is given."
    )
    lines.append(
        "- **Achieved concurrency below the configured level** means the tool "
        "could not keep its own workers busy — the load generator became the "
        "bottleneck before the server did."
    )
    lines.append(
        "- **JMeter latency is measured in whole milliseconds.** For the `echo` "
        "method, where responses are far below a millisecond, read throughput "
        "rather than latency shape. This is a property of the tool, left "
        "uncorrected on purpose."
    )
    lines.append(
        "- **Closed-loop and open-loop families are never mixed.** Threads make "
        "throughput an outcome; a target rate makes it an input. Comparing them "
        "in one table is the most common way these benchmarks mislead."
    )
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    if not (args.results_dir / "analysis.json").exists():
        print(f"no analysis.json in {args.results_dir}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(args.results_dir))
    print(f"report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
