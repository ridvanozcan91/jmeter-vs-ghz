#!/usr/bin/env bash
# Archive a run's per-request records for sharing or archival.
#
# Raw and canonical records are not committed: one run produces hundreds of
# megabytes, because ghz emits a JSON object per request. The repository keeps
# the derived statistics, and this script packages the underlying records so
# that anyone who wants to recompute from scratch can be handed them.
set -euo pipefail

RUN_DIR="${1:?usage: pack-results.sh results/<run-id> [output.tar.gz]}"
OUTPUT="${2:-${RUN_DIR%/}-records.tar.gz}"

[[ -d "${RUN_DIR}" ]] || { echo "no such run directory: ${RUN_DIR}" >&2; exit 1; }

# The manifest travels with the records: records without the environment that
# produced them cannot be argued with.
tar czf "${OUTPUT}" \
  -C "$(dirname "${RUN_DIR}")" \
  "$(basename "${RUN_DIR}")/manifest.json" \
  "$(basename "${RUN_DIR}")/normalized" \
  "$(basename "${RUN_DIR}")/raw"

echo "packed $(du -h "${OUTPUT}" | cut -f1) -> ${OUTPUT}"
echo "sha256: $(sha256sum "${OUTPUT}" | cut -d' ' -f1)"
