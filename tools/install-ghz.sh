#!/usr/bin/env bash
# Install a pinned ghz into ./.tools/bin.
#
# Built from source via the Go module proxy rather than downloaded from GitHub
# releases: the module proxy is reachable from more restricted networks, and
# building from a pinned module version is at least as reproducible as a release
# asset whose name can change between versions.
set -euo pipefail

GHZ_VERSION="${GHZ_VERSION:-0.121.0}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${REPO_ROOT}/.tools/bin"

command -v go >/dev/null || { echo "go is required to install ghz" >&2; exit 1; }

mkdir -p "${BIN_DIR}"
echo "installing ghz v${GHZ_VERSION}"

# The version is stamped in so that `ghz --version` matches the module version;
# without it a source build reports "dev" and the manifest records nothing useful.
GOBIN="${BIN_DIR}" GOFLAGS="-ldflags=-X=main.version=${GHZ_VERSION}" \
  go install "github.com/bojand/ghz/cmd/ghz@v${GHZ_VERSION}"

echo "installed: $("${BIN_DIR}/ghz" --version)"
echo "sha256:    $(sha256sum "${BIN_DIR}/ghz" | cut -d' ' -f1)"
echo
echo "export GHZ_BIN=${BIN_DIR}/ghz"
