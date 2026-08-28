# The System Under Test.
#
# Runs as an unprivileged user with no assumptions about which one: OpenShift
# assigns an arbitrary UID from the namespace's range and puts it in group 0, so
# everything the process needs is group-0 readable. See docs/OPENSHIFT.md.
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY proto/ proto/
COPY sut/pom.xml sut/pom.xml
# Resolve dependencies against the pom alone first, so that a source-only change
# does not re-download the world on every rebuild.
RUN mvn -q -B -f sut/pom.xml -DskipTests dependency:go-offline || true
COPY sut/src/ sut/src/
RUN mvn -q -B -f sut/pom.xml -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/sut/target/benchmark-sut-1.0.0.jar app.jar

# Group 0 with the owner's permissions: the container's UID is not known at
# build time, only that it will be in that group.
RUN chgrp -R 0 /app && chmod -R g=u /app

ENV HOME=/app
EXPOSE 9090 9091
USER 1001
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
