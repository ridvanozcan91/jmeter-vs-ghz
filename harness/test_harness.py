#!/usr/bin/env python3
"""Tests for the parts of the harness that fairness depends on.

These are not incidental unit tests. Each one pins down a claim the report makes
about its own neutrality, so that a change which quietly breaks the claim fails
here instead of surviving into a published number.

Run with: python3 harness/test_harness.py
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze  # noqa: E402
import normalize  # noqa: E402


class PercentileTest(unittest.TestCase):
    """The single percentile function, exercised against hand-computed values."""

    def test_nearest_rank_matches_hand_computation(self):
        values = list(range(1, 101))  # 1..100
        self.assertEqual(analyze.percentile(values, 50), 50)
        self.assertEqual(analyze.percentile(values, 90), 90)
        self.assertEqual(analyze.percentile(values, 99), 99)
        self.assertEqual(analyze.percentile(values, 100), 100)

    def test_small_samples_return_observed_values(self):
        values = [10, 20, 30]
        # Nearest rank never interpolates, so every result is a real measurement.
        for p in (1, 25, 50, 75, 99):
            self.assertIn(analyze.percentile(values, p), values)

    def test_single_value(self):
        self.assertEqual(analyze.percentile([42], 99), 42)

    def test_empty_sample_is_an_error_not_a_zero(self):
        with self.assertRaises(ValueError):
            analyze.percentile([], 50)


class TimestampSemanticsTest(unittest.TestCase):
    """ghz reports end times, JMeter reports start times; both must land aligned."""

    def test_ghz_rfc3339_nanoseconds_are_not_truncated(self):
        # datetime.fromisoformat would silently drop the final three digits.
        parsed = normalize.parse_rfc3339_ns("2026-01-02T03:04:05.123456789Z")
        self.assertEqual(parsed % 1_000_000_000, 123_456_789)

    def test_ghz_timestamp_is_treated_as_end_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "ghz.json"
            raw.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "timestamp": "2026-01-02T03:04:05.000000000Z",
                                "latency": 2_000_000,
                                "status": "OK",
                                "error": "",
                            }
                        ]
                    }
                )
            )
            meta = normalize.RunMeta("ghz", "echo", 8, 1, 0, 1)
            [record] = normalize.normalize_ghz(raw, meta)

            self.assertEqual(record["end_ns"] - record["start_ns"], 2_000_000)
            self.assertEqual(record["end_ns"], normalize.parse_rfc3339_ns(
                "2026-01-02T03:04:05.000000000Z"))

    def test_jmeter_timestamp_is_treated_as_start_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "jmeter.csv"
            with raw.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timeStamp", "elapsed", "success", "responseCode"])
                writer.writerow([1_700_000_000_000, 2, "true", "200"])

            meta = normalize.RunMeta("jmeter", "echo", 8, 8, 0, 1)
            [record] = normalize.normalize_jmeter(raw, meta)

            self.assertEqual(record["start_ns"], 1_700_000_000_000 * 1_000_000)
            self.assertEqual(record["end_ns"] - record["start_ns"], 2_000_000)

    def test_both_tools_agree_on_a_request_spanning_the_same_wall_clock(self):
        """A 5ms request ending at the same instant must normalize identically."""
        end_ns = normalize.parse_rfc3339_ns("2026-01-02T03:04:05.000000000Z")
        latency_ns = 5_000_000

        with tempfile.TemporaryDirectory() as tmp:
            ghz_raw = Path(tmp) / "ghz.json"
            ghz_raw.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "timestamp": "2026-01-02T03:04:05.000000000Z",
                                "latency": latency_ns,
                                "status": "OK",
                                "error": "",
                            }
                        ]
                    }
                )
            )
            jmeter_raw = Path(tmp) / "jmeter.csv"
            with jmeter_raw.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timeStamp", "elapsed", "success", "responseCode"])
                writer.writerow([(end_ns - latency_ns) // 1_000_000, 5, "true", "200"])

            [from_ghz] = normalize.normalize_ghz(ghz_raw, normalize.RunMeta("ghz", "echo", 1, 1, 0, 1))
            [from_jmeter] = normalize.normalize_jmeter(
                jmeter_raw, normalize.RunMeta("jmeter", "echo", 1, 1, 0, 1)
            )

            self.assertEqual(from_ghz["start_ns"], from_jmeter["start_ns"])
            self.assertEqual(from_ghz["end_ns"], from_jmeter["end_ns"])
            self.assertEqual(from_ghz["latency_ns"], from_jmeter["latency_ns"])


class StatusMappingTest(unittest.TestCase):
    def test_ghz_non_ok_status_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "ghz.json"
            raw.write_text(
                json.dumps(
                    {
                        "details": [
                            {
                                "timestamp": "2026-01-02T03:04:05Z",
                                "latency": 1,
                                "status": "DeadlineExceeded",
                                "error": "context deadline exceeded",
                            }
                        ]
                    }
                )
            )
            [record] = normalize.normalize_ghz(raw, normalize.RunMeta("ghz", "echo", 1, 1, 0, 1))
            self.assertFalse(record["ok"])
            self.assertEqual(record["status"], "DeadlineExceeded")

    def test_jmeter_failure_keeps_its_response_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "jmeter.csv"
            with raw.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timeStamp", "elapsed", "success", "responseCode"])
                writer.writerow([1_700_000_000_000, 7, "false", "500"])
            [record] = normalize.normalize_jmeter(raw, normalize.RunMeta("jmeter", "echo", 1, 1, 0, 1))
            self.assertFalse(record["ok"])
            self.assertEqual(record["status"], "500")


class WindowTest(unittest.TestCase):
    """The measured window must select the same seconds regardless of tool."""

    @staticmethod
    def _records(count: int, start_ns: int, spacing_ns: int, latency_ns: int) -> list[dict]:
        return [
            {
                "tool": "ghz",
                "method": "echo",
                "concurrency": 1,
                "connections": 1,
                "target_rps": 0,
                "repeat": 1,
                "start_ns": start_ns + i * spacing_ns,
                "end_ns": start_ns + i * spacing_ns + latency_ns,
                "latency_ns": latency_ns,
                "status": "OK",
                "ok": True,
                "latency_resolution_ns": 1,
            }
            for i in range(count)
        ]

    def test_warmup_requests_are_excluded(self):
        # 10 requests one second apart; 3s warmup, 5s measured.
        records = self._records(10, 1_000_000_000_000, 1_000_000_000, 1_000_000)
        summary = analyze.summarize(records, warmup_ns=3_000_000_000, measure_ns=5_000_000_000)
        self.assertEqual(summary["requests_total"], 5)

    def test_throughput_uses_the_window_length_not_the_run_length(self):
        records = self._records(100, 1_000_000_000_000, 10_000_000, 1_000_000)
        summary = analyze.summarize(records, warmup_ns=0, measure_ns=1_000_000_000)
        # 100 requests, 10ms apart, all inside a 1s window.
        self.assertAlmostEqual(summary["throughput_rps"], 100.0, places=6)

    def test_failures_do_not_count_toward_throughput(self):
        records = self._records(10, 1_000_000_000_000, 100_000_000, 1_000_000)
        for record in records[:5]:
            record["ok"] = False
            record["status"] = "Unavailable"
        summary = analyze.summarize(records, warmup_ns=0, measure_ns=1_000_000_000)
        self.assertEqual(summary["requests_ok"], 5)
        self.assertEqual(summary["requests_failed"], 5)
        self.assertAlmostEqual(summary["error_rate"], 0.5)

    def test_run_shorter_than_the_window_is_an_error_not_a_silent_zero(self):
        records = self._records(3, 1_000_000_000_000, 1_000_000, 1_000_000)
        with self.assertRaises(ValueError):
            analyze.summarize(records, warmup_ns=10_000_000_000, measure_ns=5_000_000_000)


class AggregationTest(unittest.TestCase):
    def test_median_not_mean_across_repeats(self):
        base = {
            "tool": "ghz",
            "method": "echo",
            "concurrency": 8,
            "connections": 1,
            "target_rps": 0,
            "latency_resolution_ns": 1,
            "error_rate": 0.0,
            "achieved_concurrency": 8.0,
            "concurrency_efficiency": 1.0,
        }
        summaries = [
            {**base, "repeat": 1, "throughput_rps": 100.0, "latency_ns": {"p99": 1000}},
            {**base, "repeat": 2, "throughput_rps": 110.0, "latency_ns": {"p99": 1100}},
            # One outlier repeat, which a mean would let move the headline number.
            {**base, "repeat": 3, "throughput_rps": 10.0, "latency_ns": {"p99": 90000}},
        ]
        result = analyze.aggregate(summaries)
        self.assertEqual(result["throughput_rps_median"], 100.0)
        self.assertEqual(result["throughput_rps_min"], 10.0)


class FamilySeparationTest(unittest.TestCase):
    """Closed-loop and open-loop runs must never merge into one scenario."""

    @staticmethod
    def _summary(target_rps: int, throughput: float) -> dict:
        return {
            "tool": "ghz",
            "method": "echo",
            "concurrency": 256,
            "connections": 8,
            "target_rps": target_rps,
            "repeat": 1,
            "throughput_rps": throughput,
            "latency_ns": {"p99": 1000},
            "latency_resolution_ns": 1,
            "error_rate": 0.0,
            "achieved_concurrency": 1.0,
            "concurrency_efficiency": 1.0,
        }

    def test_different_target_rates_are_different_scenarios(self):
        # Without target_rps in the key, a 1k run and a 10k run of the same tool
        # would average into one row that describes neither.
        low = analyze.scenario_key(self._summary(1000, 990.0))
        high = analyze.scenario_key(self._summary(10000, 9800.0))
        self.assertNotEqual(low, high)

    def test_open_loop_is_scored_on_rate_attainment(self):
        result = analyze.aggregate([self._summary(10000, 8000.0)])
        self.assertAlmostEqual(result["rate_attainment_median"], 0.8)

    def test_closed_loop_has_no_rate_attainment(self):
        result = analyze.aggregate([self._summary(0, 8000.0)])
        self.assertIsNone(result["rate_attainment_median"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
