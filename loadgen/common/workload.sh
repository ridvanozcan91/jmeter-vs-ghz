#!/usr/bin/env bash
# Builds the request JSON and full method name for a given logical method.
#
# Both load generators call this, so the payload bytes on the wire are identical
# by construction rather than by two hand-maintained copies that can drift.
# Field names are lowerCamelCase, which both protobuf JSON parsers accept.

request_json_for() {
  case "$1" in
    echo)    printf '{"message":"%s"}' "${ECHO_MESSAGE}" ;;
    compute) printf '{"delayMs":%s,"message":"%s"}' "${COMPUTE_DELAY_MS}" "${ECHO_MESSAGE}" ;;
    payload) printf '{"responseSizeBytes":%s,"payload":""}' "${PAYLOAD_SIZE_BYTES}" ;;
    *) echo "unknown method: $1" >&2; return 1 ;;
  esac
}

full_method_for() {
  case "$1" in
    echo)    printf '%s/Echo' "${GRPC_SERVICE}" ;;
    compute) printf '%s/Compute' "${GRPC_SERVICE}" ;;
    payload) printf '%s/Payload' "${GRPC_SERVICE}" ;;
    *) echo "unknown method: $1" >&2; return 1 ;;
  esac
}
