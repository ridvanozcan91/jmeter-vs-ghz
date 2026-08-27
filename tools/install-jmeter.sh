#!/usr/bin/env bash
# Install Apache JMeter and build the zalopay gRPC plugin from its source tag.
#
# The plugin is built from the git tag rather than downloaded as a release asset:
# it pins the exact commit, survives release-asset renames, and makes the one
# build-time override this benchmark needs explicit and auditable.
set -euo pipefail

JMETER_VERSION="${JMETER_VERSION:-5.6.3}"
PLUGIN_TAG="${PLUGIN_TAG:-v1.2.5.1}"
# Plugin v1.2.5.1 declares Lombok 1.18.24, which cannot compile under JDK 21
# (NoSuchFieldError: JCTree$JCImport.qualid). Only the Lombok version is
# overridden; no plugin source is modified. See docs/TUNING.md.
LOMBOK_VERSION="${LOMBOK_VERSION:-1.18.34}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="${REPO_ROOT}/.tools"
JMETER_HOME="${TOOLS_DIR}/apache-jmeter-${JMETER_VERSION}"

command -v mvn >/dev/null || { echo "maven is required to build the plugin" >&2; exit 1; }
mkdir -p "${TOOLS_DIR}"

if [[ ! -d "${JMETER_HOME}" ]]; then
  echo "downloading Apache JMeter ${JMETER_VERSION}"
  curl -fsSL -o "${TOOLS_DIR}/jmeter.tgz" \
    "https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz"
  tar xzf "${TOOLS_DIR}/jmeter.tgz" -C "${TOOLS_DIR}"
  rm -f "${TOOLS_DIR}/jmeter.tgz"
fi

SRC_DIR="${TOOLS_DIR}/jmeter-grpc-request"
if [[ ! -d "${SRC_DIR}" ]]; then
  echo "cloning jmeter-grpc-request ${PLUGIN_TAG}"
  git clone --quiet --depth 1 --branch "${PLUGIN_TAG}" \
    https://github.com/zalopay-oss/jmeter-grpc-request.git "${SRC_DIR}"
fi

echo "plugin commit: $(git -C "${SRC_DIR}" rev-parse HEAD)"
echo "building plugin (lombok override ${LOMBOK_VERSION})"
mvn -q -B -f "${SRC_DIR}/pom.xml" -DskipTests -Dlombok.version="${LOMBOK_VERSION}" package

cp "${SRC_DIR}/target/jmeter-grpc-request.jar" "${JMETER_HOME}/lib/ext/"

echo "installed plugin sha256: $(sha256sum "${JMETER_HOME}/lib/ext/jmeter-grpc-request.jar" | cut -d' ' -f1)"
echo
echo "export JMETER_HOME=${JMETER_HOME}"
