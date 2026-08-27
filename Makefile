# Convenience targets. Everything here is a thin wrapper over the scripts, which
# remain the interface: a Makefile that hides what a benchmark actually ran is a
# Makefile that makes results hard to argue with.

SHELL := /bin/bash
REPO_ROOT := $(shell pwd)
TOOLS_DIR := $(REPO_ROOT)/.tools

export GHZ_BIN ?= $(TOOLS_DIR)/bin/ghz
export JMETER_HOME ?= $(TOOLS_DIR)/apache-jmeter-5.6.3
export SUT_JAR ?= $(REPO_ROOT)/sut/target/benchmark-sut-1.0.0.jar

.PHONY: help tools build test smoke local full clean-results

help:
	@echo "tools    install pinned ghz and JMeter + plugin into .tools/"
	@echo "build    build the System Under Test"
	@echo "test     run harness unit tests"
	@echo "smoke    prove the pipeline runs end to end (not a benchmark)"
	@echo "local    constrained single-host run (directional numbers only)"
	@echo "full     the authoritative matrix; run it on two machines, see docs/RUNBOOK.md"

tools:
	tools/install-ghz.sh
	tools/install-jmeter.sh

build:
	cd sut && mvn -q -B -DskipTests package

test:
	python3 harness/test_harness.py

# Proves the chain works. Numbers from this are meaningless by design: the
# window is seconds long and one repeat cannot show variance.
smoke: build test
	harness/run-matrix.sh --profile smoke --family closed

# Single host, CPU-pinned. Directional only; see docs/RUNBOOK.md.
local: build
	SUT_CPUS=0,1 LOADGEN_CPUS=2 harness/run-matrix.sh --profile local --family closed

# Expects SUT_HOST to point at another machine. Refuses to pretend otherwise.
full:
	@if [[ "$${SUT_HOST:-127.0.0.1}" == "127.0.0.1" || "$${SUT_HOST}" == "localhost" ]]; then \
		echo "SUT_HOST is local: a full run needs the server on another machine."; \
		echo "See docs/RUNBOOK.md, or use 'make local' for a directional run."; \
		exit 1; \
	fi
	harness/run-matrix.sh --profile full --family all

clean-results:
	rm -rf results/*/raw results/*/normalized
