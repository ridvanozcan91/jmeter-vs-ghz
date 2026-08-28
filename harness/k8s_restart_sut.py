#!/usr/bin/env python3
"""Restart the SUT pod and wait for a fresh one to become ready.

The matrix restarts the server between tools so that neither tool inherits JIT
compilation paid for by the other. On a single host the harness owns the JVM and
can simply kill it; in a cluster it does not, so the equivalent action is to
delete the SUT pod and let the Deployment replace it. Without this, the first
tool to run in a cell warms the server for the second, which is precisely the
asymmetry the restart exists to remove.

Called through ``SUT_RESTART_CMD``; see docs/OPENSHIFT.md. Uses the pod's own
service account token against the Kubernetes API with nothing but the standard
library, so the load generator image needs no cluster CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class ApiError(RuntimeError):
    pass


def api_base() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    if not host:
        raise ApiError("KUBERNETES_SERVICE_HOST is unset: not running in a cluster")
    return f"https://{host}:{port}"


def read_token() -> str:
    try:
        return (SA_DIR / "token").read_text().strip()
    except OSError as exc:
        raise ApiError(f"no service account token: {exc}") from exc


def ssl_context() -> ssl.SSLContext:
    """Verify the API server against the service account's CA, never skip it."""
    ca = SA_DIR / "ca.crt"
    if not ca.exists():
        raise ApiError(f"service account CA missing at {ca}")
    return ssl.create_default_context(cafile=str(ca))


def request(method: str, path: str, token: str, context: ssl.SSLContext) -> dict:
    req = urllib.request.Request(api_base() + path, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise ApiError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"{method} {path} -> {exc.reason}") from exc
    return json.loads(body) if body else {}


def list_pods(namespace: str, selector: str, token: str, context: ssl.SSLContext) -> list[dict]:
    query = urllib.parse.urlencode({"labelSelector": selector})
    path = f"/api/v1/namespaces/{namespace}/pods?{query}"
    return request("GET", path, token, context).get("items", [])


def is_ready(pod: dict) -> bool:
    if pod.get("metadata", {}).get("deletionTimestamp"):
        return False
    if pod.get("status", {}).get("phase") != "Running":
        return False
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def restart(namespace: str, selector: str, timeout: float) -> int:
    token = read_token()
    context = ssl_context()

    before = list_pods(namespace, selector, token, context)
    old_uids = {pod["metadata"]["uid"] for pod in before}
    if not before:
        print(f"no pods match {selector} in {namespace}; nothing to restart", file=sys.stderr)

    for pod in before:
        name = pod["metadata"]["name"]
        request("DELETE", f"/api/v1/namespaces/{namespace}/pods/{name}", token, context)
        print(f"deleted pod {name}", file=sys.stderr)

    # Waiting for readiness here rather than leaving it to the caller's health
    # check matters: while the old pod is terminating it can still answer, so a
    # health probe alone would report success against the JVM being replaced.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pods = list_pods(namespace, selector, token, context)
        fresh = [p for p in pods if p["metadata"]["uid"] not in old_uids]
        stale = [p for p in pods if p["metadata"]["uid"] in old_uids]
        if fresh and not stale and all(is_ready(p) for p in fresh):
            names = ", ".join(p["metadata"]["name"] for p in fresh)
            print(f"SUT replaced and ready: {names}", file=sys.stderr)
            return 0
        time.sleep(2)

    print(f"SUT did not become ready within {timeout:g}s", file=sys.stderr)
    return 1


def print_node(namespace: str, selector: str) -> int:
    """Print the node the SUT is running on.

    The report refuses to treat a run as authoritative when both pods landed on
    the same node, and it can only check that if the load generator records
    where the server was. The API is the only place that knows.
    """
    token = read_token()
    context = ssl_context()
    nodes = sorted(
        {
            pod.get("spec", {}).get("nodeName", "")
            for pod in list_pods(namespace, selector, token, context)
            if is_ready(pod)
        }
        - {""}
    )
    if not nodes:
        print(f"no ready pod matches {selector} in {namespace}", file=sys.stderr)
        return 1
    print(",".join(nodes))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--selector",
        default="app=benchmark-sut",
        help="label selector for the SUT pods (default: app=benchmark-sut)",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="namespace to act in (default: the pod's own namespace)",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--print-node",
        action="store_true",
        help="print the node the SUT runs on and exit, restarting nothing",
    )
    args = parser.parse_args(argv)

    namespace = args.namespace
    if namespace is None:
        try:
            namespace = (SA_DIR / "namespace").read_text().strip()
        except OSError as exc:
            print(f"cannot determine namespace: {exc}", file=sys.stderr)
            return 2

    try:
        if args.print_node:
            return print_node(namespace, args.selector)
        return restart(namespace, args.selector, args.timeout)
    except ApiError as exc:
        print(f"restart failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
