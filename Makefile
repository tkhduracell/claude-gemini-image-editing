# gemini-image — build, test, and image-publishing targets.

IMAGE   := tkhduracell/gemini-image
# Keep VERSION in sync with "version" in .claude-plugin/plugin.json and with the
# image tag pinned in that file's mcpServers args.
VERSION := 0.2.0

.PHONY: all help install run test lint clean docker-build docker-buildx docker-push validate

all: help

help:
	@echo "========================================================="
	@echo " gemini-image - Makefile"
	@echo "========================================================="
	@echo "Available commands:"
	@echo "  make install       - Install Python requirements"
	@echo "  make run           - Start the FastMCP server (server.py)"
	@echo "  make test          - Run the agent integration tests (needs a key)"
	@echo "  make lint          - ruff check + ruff format --check on the sources"
	@echo "  make clean         - Remove Python caches"
	@echo "  make docker-build  - Build $(IMAGE):$(VERSION) locally (host arch)"
	@echo "  make docker-buildx - Multi-arch build (amd64+arm64), no push"
	@echo "  make docker-push   - Multi-arch build + push $(VERSION) and latest"
	@echo "                       (requires: docker login)"
	@echo "  make validate      - Validate the .claude-plugin manifests"
	@echo ""
	@echo "VERSION = $(VERSION) (must match .claude-plugin/plugin.json)"
	@echo "========================================================="

install:
	pip install -r requirements.txt

run:
	python server.py

test:
	python test_agent.py

lint:
	@command -v ruff >/dev/null || { echo "ruff not found; install with: pip install ruff"; exit 1; }
	ruff check server.py test_agent.py
	ruff format --check server.py test_agent.py
	@echo "lint OK"

clean:
	rm -rf .ruff_cache .mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +

# The image holds only server.py + requirements.txt (see .dockerignore — key
# files can never enter the build context).

# Plain local build: single arch, loaded into the local daemon. What a dev wants
# when iterating; needs no registry login.
docker-build:
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

# Multi-arch, build only. Verifies both platforms compile without publishing.
docker-buildx:
	docker buildx build --platform linux/amd64,linux/arm64 \
		-t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

# Multi-arch publish. buildx cannot --load a multi-platform result, so this
# pushes straight to the registry: run `docker login` first.
docker-push:
	docker buildx build --platform linux/amd64,linux/arm64 \
		-t $(IMAGE):$(VERSION) -t $(IMAGE):latest --push .

validate:
	claude plugin validate .
