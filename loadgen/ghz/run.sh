#!/usr/bin/env bash
# Runs one ghz scenario and writes raw per-request records.
#
# Usage: run.sh <method> <concurrency> <duration_s> <out_dir> [connections] [rps]
#
# Fairness notes:
#   - Templating is disabled so ghz does not pay (or avoid) per-request work
#     that differs from what the JMeter plugin does. What each tool spends per
#     request is a finding, not something we silently equalise.
#   - --skipFirst is NOT used. The measurement window is cut from raw timestamps
#     by the harness, identically for both tools.
#   - The per-request deadline matches the JMeter sampler's deadline exactly.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/env.sh
source "${HERE}/../common/env.sh"
# shellcheck source=../common/workload.sh
source "${HERE}/../common/workload.sh"

METHOD="${1:?method required}"
CONCURRENCY="${2:?concurrency required}"
DURATION_S="${3:?duration required}"
OUT_DIR="${4:?out dir required}"
CONNECTIONS="${5:-${CONCURRENCY}}"
RPS="${6:-0}"

GHZ_BIN="${GHZ_BIN:-ghz}"
mkdir -p "${OUT_DIR}"

args=(
  --insecure
  --proto "${PROTO_ROOT}/${PROTO_FILE}"
  --import-paths "${PROTO_ROOT}"
  --call "$(full_method_for "${METHOD}")"
  --data "$(request_json_for "${METHOD}")"
  --concurrency "${CONCURRENCY}"
  --connections "${CONNECTIONS}"
  --duration "${DURATION_S}s"
  --duration-stop wait
  --timeout "${REQUEST_TIMEOUT_MS}ms"
  --disable-template-data
  --disable-template-functions
  --count-errors
  --format json
  --output "${OUT_DIR}/ghz.json"
  --name "${METHOD}-c${CONCURRENCY}-conn${CONNECTIONS}"
)

# Open-loop mode. Left unset for the closed-loop family, where throughput must
# be an outcome rather than an input, exactly as it is for JMeter threads.
if [[ "${RPS}" != "0" ]]; then
  args+=(--rps "${RPS}")
fi

"${GHZ_BIN}" "${args[@]}" "${SUT_TARGET}"
