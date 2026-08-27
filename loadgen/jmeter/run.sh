#!/usr/bin/env bash
# Runs one JMeter scenario and writes raw per-sample records.
#
# Usage: run.sh <method> <threads> <duration_s> <out_dir> [target_rps]
#
# Fairness notes:
#   - Non-GUI mode, no listeners in the plan, and the result CSV keeps only the
#     fields the harness needs. Result rendering must not be charged to the
#     sampling threads.
#   - The sampler deadline matches ghz's --timeout exactly.
#   - Heap is sized generously and the same JVM flags are used for every run, so
#     no concurrency level is handicapped by GC configuration.
#   - Every tuning decision here is documented in docs/TUNING.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/env.sh
source "${HERE}/../common/env.sh"
# shellcheck source=../common/workload.sh
source "${HERE}/../common/workload.sh"

METHOD="${1:?method required}"
THREADS="${2:?threads required}"
DURATION_S="${3:?duration required}"
OUT_DIR="${4:?out dir required}"
TARGET_RPS="${5:-0}"

JMETER_HOME="${JMETER_HOME:?JMETER_HOME must point at an Apache JMeter installation}"

# A target rate selects the open-loop plan, which paces requests with a Precise
# Throughput Timer. Without one, throughput must be an outcome of thread count,
# so the closed-loop plan is used and carries no timer at all.
if [[ -n "${JMETER_PLAN:-}" ]]; then
  PLAN="${JMETER_PLAN}"
elif [[ "${5:-0}" != "0" ]]; then
  PLAN="${HERE}/plans/open-loop.jmx"
else
  PLAN="${HERE}/plans/closed-loop.jmx"
fi
mkdir -p "${OUT_DIR}"

# JMeter's own JVM. Kept identical across every run of the matrix.
export HEAP="${JMETER_HEAP:--Xms2g -Xmx4g -XX:+UseG1GC -XX:MaxGCPauseMillis=100}"

save_opts=(
  -Jjmeter.save.saveservice.output_format=csv
  -Jjmeter.save.saveservice.timestamp_format=ms
  -Jjmeter.save.saveservice.time=true
  -Jjmeter.save.saveservice.label=true
  -Jjmeter.save.saveservice.response_code=true
  -Jjmeter.save.saveservice.response_message=false
  -Jjmeter.save.saveservice.successful=true
  -Jjmeter.save.saveservice.thread_counts=true
  -Jjmeter.save.saveservice.thread_name=false
  -Jjmeter.save.saveservice.latency=true
  -Jjmeter.save.saveservice.connect_time=true
  -Jjmeter.save.saveservice.bytes=true
  -Jjmeter.save.saveservice.sent_bytes=false
  -Jjmeter.save.saveservice.assertion_results_failure_message=false
  -Jjmeter.save.saveservice.idle_time=false
  -Jjmeter.save.saveservice.hostname=false
  -Jjmeter.save.saveservice.url=false
  -Jjmeter.save.saveservice.sample_count=false
  -Jjmeter.save.saveservice.subresults=false
  -Jjmeter.save.saveservice.assertions=false
)

args=(
  -n -t "${PLAN}"
  -l "${OUT_DIR}/jmeter.csv"
  -j "${OUT_DIR}/jmeter.log"
  -Jthreads="${THREADS}"
  -Jrampup=0
  -Jduration="${DURATION_S}"
  -Jhost="${SUT_HOST}"
  -Jport="${SUT_GRPC_PORT}"
  -JprotoFolder="${PROTO_ROOT}"
  -JfullMethod="$(full_method_for "${METHOD}")"
  -JrequestJson="$(request_json_for "${METHOD}")"
  -Jdeadline="${REQUEST_TIMEOUT_MS}"
  -JmaxInboundMessageSize=16777216
  -Jsummariser.name=
)

if [[ "${TARGET_RPS}" != "0" ]]; then
  args+=(-JtargetRps="${TARGET_RPS}")
fi

rm -f "${OUT_DIR}/jmeter.csv"
"${JMETER_HOME}/bin/jmeter" "${save_opts[@]}" "${args[@]}"
