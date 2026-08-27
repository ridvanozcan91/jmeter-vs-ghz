# Load generator image: JMeter with the plugin, ghz, and the harness.
#
# Untested: written in an environment with no Docker daemon. See deploy/README.md.
#
# Both tools live in one image on purpose. Separate images would drift — a
# different base, a different JDK, a different set of OS tunables — and the
# benchmark would then be comparing environments as much as tools.
FROM eclipse-temurin:21-jdk

ARG GHZ_VERSION=0.121.0
ARG JMETER_VERSION=5.6.3
ARG PLUGIN_TAG=v1.2.5.1

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl git maven golang-go python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY tools/ tools/

# Both installers pin their versions and print the sha256 of what they produced,
# which the run manifest records so an image can be tied to its results.
RUN GHZ_VERSION=${GHZ_VERSION} tools/install-ghz.sh \
    && JMETER_VERSION=${JMETER_VERSION} PLUGIN_TAG=${PLUGIN_TAG} tools/install-jmeter.sh

COPY proto/ proto/
COPY harness/ harness/
COPY loadgen/ loadgen/

ENV GHZ_BIN=/app/.tools/bin/ghz
ENV JMETER_HOME=/app/.tools/apache-jmeter-5.6.3

ENTRYPOINT ["harness/run-matrix.sh"]
CMD ["--profile", "full", "--family", "all"]
