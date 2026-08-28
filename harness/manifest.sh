#!/usr/bin/env bash
# Emit a reproducibility manifest as JSON on stdout.
#
# A benchmark number without the environment that produced it is an anecdote.
# Everything here is captured automatically so that a result committed to this
# repository can be argued with on the basis of how it was produced.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"

json_string() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'; }
sha_of() { [[ -f "$1" ]] && sha256sum "$1" | cut -d" " -f1 || echo "unavailable"; }

# In a container nproc reports the node's cores, not the pod's budget. Recording
# the cgroup quota alongside it is what stops a reader from dividing throughput
# by a core count the load generator never had. null when unlimited.
cgroup_cpu_limit() {
  local quota period
  if [[ -r /sys/fs/cgroup/cpu.max ]]; then
    read -r quota period < /sys/fs/cgroup/cpu.max
    [[ "${quota}" == "max" ]] && { echo null; return; }
  elif [[ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]]; then
    quota="$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)"
    period="$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)"
    [[ "${quota}" -le 0 ]] && { echo null; return; }
  else
    echo null; return
  fi
  awk -v q="${quota}" -v p="${period}" 'BEGIN { printf "%.3f", q / p }'
}

GHZ_BIN="${GHZ_BIN:-ghz}"
JMETER_HOME="${JMETER_HOME:-}"

cat <<EOF
{
  "run_id": $(echo "${RUN_ID:-unknown}" | json_string),
  "captured_at_utc": $(date -u +%Y-%m-%dT%H:%M:%SZ | json_string),
  "git": {
    "commit": $( { git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null \
                    || echo "${BENCHMARK_GIT_COMMIT:-unknown}"; } | json_string),
    "dirty": $(if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null)" ]]; then echo true; else echo false; fi)
  },
  "proto_sha256": $(sha_of "${REPO_ROOT}/proto/benchmark/v1/benchmark.proto" | json_string),
  "host": {
    "kernel": $(uname -sr | json_string),
    "node_name": $(echo "${NODE_NAME:-}" | json_string),
    "sut_node_name": $(echo "${SUT_NODE_NAME:-}" | json_string),
    "cgroup_cpu_limit_cores": $(cgroup_cpu_limit),
    "cpu_model": $(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2- | json_string),
    "cpu_count": $(nproc),
    "memory_kb": $(awk '/MemTotal/ {print $2}' /proc/meminfo),
    "ulimit_nofile": $(ulimit -n),
    "somaxconn": $(cat /proc/sys/net/core/somaxconn 2>/dev/null || echo 0)
  },
  "tools": {
    "java": $(java -version 2>&1 | grep -m1 -E "version \"" | json_string),
    "ghz": $("${GHZ_BIN}" --version 2>&1 | grep -m1 -oE "[0-9]+\.[0-9]+\.[0-9]+" | json_string),
    "ghz_binary_sha256": $(sha_of "$(command -v "${GHZ_BIN}" || echo /nonexistent)" | json_string),
    "jmeter_home": $(echo "${JMETER_HOME}" | json_string),
    "jmeter_plugin_sha256": $(sha_of "${JMETER_HOME}/lib/ext/jmeter-grpc-request.jar" | json_string),
    "loadgen_image": $(echo "${LOADGEN_IMAGE:-}" | json_string)
  },
  "target": {
    "host": $(echo "${SUT_HOST:-}" | json_string),
    "grpc_port": $(echo "${SUT_GRPC_PORT:-0}"),
    "same_host_as_loadgen": $(if [[ "${SUT_HOST:-127.0.0.1}" == "127.0.0.1" || "${SUT_HOST:-}" == "localhost" ]]; then echo true; else echo false; fi)
  },
  "scenario": {
    "warmup_seconds": ${WARMUP_SECONDS:-0},
    "measure_seconds": ${MEASURE_SECONDS:-0},
    "repeats": ${REPEATS:-0},
    "request_timeout_ms": ${REQUEST_TIMEOUT_MS:-0},
    "concurrency_levels": $(echo "${CONCURRENCY_LEVELS:-}" | json_string),
    "methods": $(echo "${METHODS:-}" | json_string),
    "sut_cpus": $(echo "${SUT_CPUS:-}" | json_string),
    "loadgen_cpus": $(echo "${LOADGEN_CPUS:-}" | json_string)
  }
}
EOF
