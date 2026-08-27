# Load generator image: JMeter with the plugin, ghz, and the harness.
#
# Both tools live in one image on purpose. Separate images would drift — a
# different base, a different JDK, a different set of OS tunables — and the
# benchmark would then be comparing environments as much as tools.
#
# The build toolchains stay in their own stages. Only what is needed to run load
# reaches the final image, which keeps the layer that has to cross a corporate
# network to a registry as small as it can be, and keeps a compiler out of the
# image that generates the load.
#
# Runs as an unprivileged user with no assumptions about which one: OpenShift
# assigns an arbitrary UID from the namespace's range and puts it in group 0.
# See docs/OPENSHIFT.md.

ARG GHZ_VERSION=0.121.0
ARG JMETER_VERSION=5.6.3
ARG PLUGIN_TAG=v1.2.5.1

# --- ghz -------------------------------------------------------------------
# A Go image rather than the distribution's golang package: the pinned ghz
# version needs a newer toolchain than Debian stable carries.
FROM golang:1.23-bookworm AS ghz-build
ARG GHZ_VERSION
WORKDIR /build
COPY tools/install-ghz.sh tools/
RUN GHZ_VERSION=${GHZ_VERSION} tools/install-ghz.sh

# --- JMeter and the plugin --------------------------------------------------
FROM maven:3.9-eclipse-temurin-21 AS jmeter-build
ARG JMETER_VERSION
ARG PLUGIN_TAG
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY tools/install-jmeter.sh tools/
RUN JMETER_VERSION=${JMETER_VERSION} PLUGIN_TAG=${PLUGIN_TAG} tools/install-jmeter.sh

# --- Runtime ----------------------------------------------------------------
FROM eclipse-temurin:21-jre
ARG JMETER_VERSION

# python3 for the harness (standard library only, no pip), curl for the SUT
# health check, gawk for the run-order shuffle and the manifest, procps for
# debugging a run in progress.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl python3 gawk procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Both installers print the sha256 of what they produced, and the run manifest
# records the same hashes, so an image can be tied to its results.
COPY --from=ghz-build /build/.tools/bin/ghz .tools/bin/ghz
COPY --from=jmeter-build /build/.tools/apache-jmeter-${JMETER_VERSION}/ \
     .tools/apache-jmeter-${JMETER_VERSION}/
COPY proto/ proto/
COPY harness/ harness/
COPY loadgen/ loadgen/

ENV GHZ_BIN=/app/.tools/bin/ghz
ENV JMETER_HOME=/app/.tools/apache-jmeter-${JMETER_VERSION}
# An arbitrary UID has no home directory of its own; without this the JVM and
# any tool that expands ~ would write to a path it cannot create.
ENV HOME=/app

# Recorded in every run manifest. A container has no git checkout, so without
# this a cluster run could not be tied back to the source that produced it.
ARG GIT_COMMIT=unknown
ENV BENCHMARK_GIT_COMMIT=${GIT_COMMIT}

RUN mkdir -p /app/results && chgrp -R 0 /app && chmod -R g=u /app

USER 1001
ENTRYPOINT ["harness/run-matrix.sh"]
CMD ["--profile", "full", "--family", "all"]
