#!/usr/bin/env bash
# Single source of truth for every scenario parameter.
#
# Both load generators read their settings from here, so no tool can silently
# receive a different workload, deadline, or measurement window than the other.
# Anything that differs between tools is either structural (documented in
# docs/ARCHITECTURE.md) or a deliberate scenario dimension.

set -euo pipefail

# --- Target -----------------------------------------------------------------
export SUT_HOST="${SUT_HOST:-127.0.0.1}"
export SUT_GRPC_PORT="${SUT_GRPC_PORT:-9090}"
export SUT_MANAGEMENT_PORT="${SUT_MANAGEMENT_PORT:-9091}"
export SUT_TARGET="${SUT_HOST}:${SUT_GRPC_PORT}"
export SUT_METRICS_URL="http://${SUT_HOST}:${SUT_MANAGEMENT_PORT}/actuator/prometheus"

# --- Workload contract (identical for both tools) ---------------------------
export PROTO_ROOT="${PROTO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../proto" && pwd)}"
export PROTO_FILE="benchmark/v1/benchmark.proto"
export GRPC_SERVICE="benchmark.v1.BenchmarkService"

# Per-request deadline. Applied to ghz (--timeout) and to the JMeter sampler
# (deadline field) with the same value, so neither tool gets a longer rope.
export REQUEST_TIMEOUT_MS="${REQUEST_TIMEOUT_MS:-20000}"

# --- Measurement window -----------------------------------------------------
# Warmup exists for the JVM on both the server and the JMeter side. The measured
# window is cut from raw per-request records by wall-clock timestamp, so both
# tools are scored over exactly the same seconds.
export WARMUP_SECONDS="${WARMUP_SECONDS:-30}"
export MEASURE_SECONDS="${MEASURE_SECONDS:-60}"
export COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-15}"
export REPEATS="${REPEATS:-5}"

# --- Scenario matrix --------------------------------------------------------
# Family A: closed loop. JMeter N threads vs ghz -c N with no rate limit.
export CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-1 8 32 64 128 256 512}"

# ghz connection modes. JMeter's plugin opens one channel per thread, so
# ghz --connections N is the like-for-like topology; 8 and 1 quantify what
# HTTP/2 multiplexing is worth on top of that.
export GHZ_CONNECTION_MODES="${GHZ_CONNECTION_MODES:-match 8 1}"

# Methods under test.
export METHODS="${METHODS:-echo compute payload}"
export COMPUTE_DELAY_MS="${COMPUTE_DELAY_MS:-50}"
export PAYLOAD_SIZE_BYTES="${PAYLOAD_SIZE_BYTES:-65536}"
export ECHO_MESSAGE="${ECHO_MESSAGE:-benchmark}"

# Family B: open loop rate sweep (ghz --rps vs JMeter throughput timer).
export RATE_LEVELS="${RATE_LEVELS:-1000 2500 5000 10000 20000 40000}"
export OPEN_LOOP_CONCURRENCY="${OPEN_LOOP_CONCURRENCY:-256}"

# --- Profiles ---------------------------------------------------------------
# smoke: proves the pipeline runs. local: the constrained run this repository
# ships as validation. full: the authoritative matrix, for a two-machine setup.
apply_profile() {
  case "${1:-full}" in
    smoke)
      export WARMUP_SECONDS=3 MEASURE_SECONDS=5 COOLDOWN_SECONDS=2 REPEATS=1
      export CONCURRENCY_LEVELS="8" METHODS="echo" GHZ_CONNECTION_MODES="match 1"
      export RATE_LEVELS="1000"
      ;;
    local)
      export WARMUP_SECONDS=8 MEASURE_SECONDS=15 COOLDOWN_SECONDS=3 REPEATS=3
      export CONCURRENCY_LEVELS="8 32 128" METHODS="echo compute"
      export GHZ_CONNECTION_MODES="match 1"
      export RATE_LEVELS="1000 5000"
      ;;
    full) ;;
    *)
      echo "unknown profile: $1" >&2
      return 1
      ;;
  esac
}
