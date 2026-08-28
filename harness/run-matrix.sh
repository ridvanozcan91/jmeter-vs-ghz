#!/usr/bin/env bash
# Orchestrate the benchmark matrix.
#
# Usage: run-matrix.sh [--profile smoke|local|full] [--family closed|open|all]
#
# The ordering rules below exist to keep the comparison honest, and each one
# costs wall-clock time on purpose:
#
#   * Tool order is shuffled within every round, so a machine that drifts warmer
#     or busier over the session does not systematically favour whichever tool
#     always ran first.
#   * The SUT is restarted between tools and given an identical warmup, so no
#     tool inherits JIT compilation paid for by the other.
#   * Every run records a before/after server metrics snapshot and a client
#     resource sample, so "who saturated first" is answerable afterwards.
#   * Raw output is kept. Nothing is summarised in place, and every published
#     number can be recomputed from what is committed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
# shellcheck source=../loadgen/common/env.sh
source "${REPO_ROOT}/loadgen/common/env.sh"

PROFILE="full"
FAMILY="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --family)  FAMILY="$2";  shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
apply_profile "${PROFILE}"

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RESULTS_DIR="${RESULTS_DIR:-${REPO_ROOT}/results/${RUN_ID}}"
RAW_DIR="${RESULTS_DIR}/raw"
CANONICAL_DIR="${RESULTS_DIR}/normalized"
mkdir -p "${RAW_DIR}" "${CANONICAL_DIR}"

GHZ_BIN="${GHZ_BIN:-ghz}"
JMETER_HOME="${JMETER_HOME:?JMETER_HOME must point at an Apache JMeter installation}"

# Optional CPU pinning. On a single machine this is the minimum needed to stop
# the load generator from stealing the server's cores; on the two-machine setup
# this repository targets, leave both unset.
SUT_CPUS="${SUT_CPUS:-}"
LOADGEN_CPUS="${LOADGEN_CPUS:-}"

# How the SUT is (re)started between tools. With no jar and no restart command
# the server is assumed to be running and managed elsewhere, which is the
# two-machine case.
#
# SUT_RESTART_CMD covers the cluster case, where the harness does not own the
# JVM but can still ask for a fresh one -- see harness/k8s_restart_sut.py. It is
# not a convenience: without a restart, the first tool to run in a cell warms the
# server for the second, and the second inherits JIT compilation it did not pay
# for.
SUT_JAR="${SUT_JAR:-}"
SUT_RESTART_CMD="${SUT_RESTART_CMD:-}"
SUT_JVM_OPTS="${SUT_JVM_OPTS:--XX:+UseG1GC -Xms2g -Xmx4g}"
SUT_PID=""

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

pin() {
  # Prefix a command with taskset when a CPU set was requested.
  local cpus="$1"; shift
  if [[ -n "${cpus}" ]]; then
    taskset -c "${cpus}" "$@"
  else
    "$@"
  fi
}

wait_for_sut() {
  local deadline=$((SECONDS + 120))
  while ((SECONDS < deadline)); do
    if curl -sf -m 3 "http://${SUT_HOST}:${SUT_MANAGEMENT_PORT}/actuator/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "SUT did not become healthy in time" >&2
  return 1
}

start_sut() {
  if [[ -z "${SUT_JAR}" ]]; then
    if [[ -n "${SUT_RESTART_CMD}" ]]; then
      log "restarting SUT via SUT_RESTART_CMD"
      # A failed restart is reported but not fatal: losing the JIT isolation for
      # one cell is worth less than losing the rest of a multi-hour matrix, and
      # the log says which cells were affected.
      bash -c "${SUT_RESTART_CMD}" >>"${RESULTS_DIR}/sut-restart.log" 2>&1 \
        || log "WARNING: SUT restart command failed; see sut-restart.log"
    fi
    wait_for_sut
    return
  fi
  stop_sut
  log "starting SUT"
  # shellcheck disable=SC2086
  pin "${SUT_CPUS}" java ${SUT_JVM_OPTS} -jar "${SUT_JAR}" >"${RESULTS_DIR}/sut.log" 2>&1 &
  SUT_PID=$!
  wait_for_sut
}

stop_sut() {
  [[ -z "${SUT_PID}" ]] && return 0
  kill "${SUT_PID}" 2>/dev/null || true
  wait "${SUT_PID}" 2>/dev/null || true
  SUT_PID=""
}
trap stop_sut EXIT

# Warm the server identically before every measured tool, so that neither tool
# is the one that pays for JIT compilation on behalf of the other.
warm_sut() {
  local method="$1"
  log "warming SUT via ${method}"
  "${REPO_ROOT}/loadgen/ghz/run.sh" "${method}" 16 "${WARMUP_SECONDS}" \
    "${RAW_DIR}/_warmup" 4 >/dev/null 2>&1 || true
  rm -rf "${RAW_DIR}/_warmup"
}

# Deterministic shuffle, varied per cell.
#
# The first argument is a salt identifying the cell. Seeding on the run id alone
# would produce the same permutation on every call, so one tool would run first
# in every single cell -- which is exactly the systematic bias the shuffle exists
# to remove. Mixing the cell coordinates into the seed keeps the order varied
# while staying reproducible for a given run id.
shuffle() {
  local salt="$1"; shift
  local seed
  seed="$(cksum <<<"${RUN_ID}-${salt}" | cut -d' ' -f1)"
  printf '%s\n' "$@" | awk -v seed="${seed}" '
    BEGIN { srand(seed) } { print rand() "\t" $0 }' | sort -k1,1 | cut -f2-
}

run_one() {
  local tool="$1" method="$2" concurrency="$3" connections="$4" repeat="$5" rps="${6:-0}"
  local tag="${tool}_${method}_c${concurrency}_conn${connections}_r${repeat}"
  [[ "${rps}" != "0" ]] && tag="${tag}_rps${rps}"
  local out="${RAW_DIR}/${tag}"
  mkdir -p "${out}"

  log "run ${tag}"
  python3 "${HERE}/scrape_server.py" --url "${SUT_METRICS_URL}" --phase before \
    --output "${out}/server-before.json" >/dev/null

  # The load generator runs long enough to cover warmup plus the measured
  # window; the window itself is cut later from raw timestamps.
  local duration=$((WARMUP_SECONDS + MEASURE_SECONDS))
  local raw_file

  if [[ "${tool}" == "jmeter" ]]; then
    raw_file="${out}/jmeter.csv"
    pin "${LOADGEN_CPUS}" env JMETER_HOME="${JMETER_HOME}" \
      "${REPO_ROOT}/loadgen/jmeter/run.sh" "${method}" "${concurrency}" "${duration}" \
      "${out}" "${rps}" >"${out}/tool.log" 2>&1 &
  else
    raw_file="${out}/ghz.json"
    pin "${LOADGEN_CPUS}" env GHZ_BIN="${GHZ_BIN}" \
      "${REPO_ROOT}/loadgen/ghz/run.sh" "${method}" "${concurrency}" "${duration}" \
      "${out}" "${connections}" "${rps}" >"${out}/tool.log" 2>&1 &
  fi
  local tool_pid=$!

  python3 "${HERE}/sample_proc.py" --pid "${tool_pid}" --interval 0.5 \
    --output "${out}/client-resources.json" >/dev/null 2>&1 &
  local sampler_pid=$!

  # Connections held and RPCs in flight are gauges: they are back at zero by the
  # time the run ends, so they have to be watched while the run is happening.
  python3 "${HERE}/scrape_server.py" --url "${SUT_METRICS_URL}" --phase series \
    --interval 0.5 --output "${out}/server-series.json" >/dev/null 2>&1 &
  local series_pid=$!

  local tool_status=0
  wait "${tool_pid}" || tool_status=$?
  kill "${sampler_pid}" 2>/dev/null || true
  wait "${sampler_pid}" 2>/dev/null || true
  kill -TERM "${series_pid}" 2>/dev/null || true
  wait "${series_pid}" 2>/dev/null || true

  python3 "${HERE}/scrape_server.py" --url "${SUT_METRICS_URL}" --phase after \
    --before-file "${out}/server-before.json" --output "${out}/server-after.json" >/dev/null

  if [[ "${tool_status}" -ne 0 ]]; then
    log "WARNING: ${tag} exited with status ${tool_status}; raw output kept for inspection"
  fi

  if [[ -s "${raw_file}" ]]; then
    python3 "${HERE}/normalize.py" --input "${raw_file}" \
      --output "${CANONICAL_DIR}/${tag}.csv" --tool "${tool}" --method "${method}" \
      --concurrency "${concurrency}" --connections "${connections}" \
      --target-rps "${rps}" --repeat "${repeat}" \
      >/dev/null || log "WARNING: could not normalize ${tag}"
  else
    log "WARNING: ${tag} produced no raw output"
  fi

  sleep "${COOLDOWN_SECONDS}"
}

ghz_connections_for() {
  # "match" reproduces the JMeter plugin's one-channel-per-thread topology, so
  # the two tools can be compared on equal footing before multiplexing is
  # allowed to help ghz.
  local mode="$1" concurrency="$2"
  [[ "${mode}" == "match" ]] && echo "${concurrency}" || echo "${mode}"
}

run_closed_loop() {
  for method in ${METHODS}; do
    for concurrency in ${CONCURRENCY_LEVELS}; do
      for repeat in $(seq 1 "${REPEATS}"); do
        local plan=()
        plan+=("jmeter:${concurrency}")
        for mode in ${GHZ_CONNECTION_MODES}; do
          plan+=("ghz-conn$(ghz_connections_for "${mode}" "${concurrency}"):${concurrency}")
        done

        while read -r entry; do
          local tool_spec="${entry%%:*}"
          if [[ "${tool_spec}" == "jmeter" ]]; then
            start_sut; warm_sut "${method}"
            run_one jmeter "${method}" "${concurrency}" "${concurrency}" "${repeat}"
          else
            local connections="${tool_spec#ghz-conn}"
            start_sut; warm_sut "${method}"
            run_one ghz "${method}" "${concurrency}" "${connections}" "${repeat}"
          fi
        done < <(shuffle "${method}-${concurrency}-${repeat}" "${plan[@]}")
      done
    done
  done
}

run_open_loop() {
  # Open loop is reported as its own family. JMeter threads and a ghz rate limit
  # are not interchangeable: one makes throughput an outcome, the other makes it
  # an input. Mixing them into one table is the most common way these
  # comparisons mislead, so the families stay separate.
  #
  # Both tools are driven toward the same target rates, with the same standby
  # concurrency. They reach a rate by different mechanisms -- a Precise
  # Throughput Timer against a token-bucket limiter -- so the comparison is
  # between achieved rate and target, not between the pacing mechanisms.
  for rate in ${RATE_LEVELS}; do
    for repeat in $(seq 1 "${REPEATS}"); do
      while read -r tool; do
        start_sut; warm_sut echo
        if [[ "${tool}" == "jmeter" ]]; then
          run_one jmeter echo "${OPEN_LOOP_CONCURRENCY}" "${OPEN_LOOP_CONCURRENCY}" \
            "${repeat}" "${rate}"
        else
          run_one ghz echo "${OPEN_LOOP_CONCURRENCY}" 8 "${repeat}" "${rate}"
        fi
      done < <(shuffle "open-${rate}-${repeat}" jmeter ghz)
    done
  done
}

log "run id ${RUN_ID}, profile ${PROFILE}, family ${FAMILY}"
"${HERE}/manifest.sh" > "${RESULTS_DIR}/manifest.json"

case "${FAMILY}" in
  closed) run_closed_loop ;;
  open)   run_open_loop ;;
  all)    run_closed_loop; run_open_loop ;;
  *) echo "unknown family: ${FAMILY}" >&2; exit 2 ;;
esac

python3 "${HERE}/analyze.py" --canonical-dir "${CANONICAL_DIR}" \
  --output "${RESULTS_DIR}/analysis.json" \
  --warmup-seconds "${WARMUP_SECONDS}" --measure-seconds "${MEASURE_SECONDS}"

python3 "${HERE}/report.py" --results-dir "${RESULTS_DIR}" --output "${RESULTS_DIR}/report.md"

log "done: ${RESULTS_DIR}/report.md"
