# Untested: written in an environment with no Docker daemon. See deploy/README.md.
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
EXPOSE 9090 9091
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
