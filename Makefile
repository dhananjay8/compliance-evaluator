# Compliance Evaluator — developer commands (plain podman; no compose provider needed)
SHELL := /bin/bash
IMAGE   := localhost/compliance-evaluator:local
APP     := compliance-evaluator
OLLAMA  := compliance-ollama
ES      := compliance-es
NET     := compliance-net
ES_URL_IN_NET ?= http://$(ES):9200
OLLAMA_MODEL  ?= qwen2.5:3b
EMBED_MODEL   ?= nomic-embed-text

.PHONY: help up down build logs ollama-pull es seed indices fixtures test test-unit test-integration e2e clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

# ---------- containers ----------
up: ## Start Ollama + app on compliance-net (needs ES on the network; see `make es`)
	podman network inspect $(NET) >/dev/null 2>&1 || podman network create $(NET)
	podman run -d --replace --name $(OLLAMA) --network $(NET) \
	  -p 11434:11434 -v compliance-ollama-models:/root/.ollama \
	  docker.io/ollama/ollama:latest
	$(MAKE) build
	podman run -d --replace --name $(APP) --network $(NET) \
	  -p 8090:8090 \
	  -e ELASTICSEARCH_URL=$(ES_URL_IN_NET) \
	  -e OLLAMA_URL=http://$(OLLAMA):11434 \
	  -e OLLAMA_MODEL=$(OLLAMA_MODEL) \
	  -e EMBEDDING_PROVIDER=ollama -e EMBEDDING_MODEL=$(EMBED_MODEL) -e EMBEDDING_DIMS=768 \
	  -e UPLOAD_DIR=/data/uploads \
	  -v compliance-uploads:/data/uploads \
	  $(IMAGE)
	@echo "app: http://localhost:8090  (make ollama-pull once, then make seed)"

es: ## Start a local Elasticsearch container on the network
	podman network inspect $(NET) >/dev/null 2>&1 || podman network create $(NET)
	podman run -d --replace --name $(ES) --network $(NET) \
	  -p 9200:9200 \
	  -e discovery.type=single-node -e xpack.security.enabled=false \
	  -e ES_JAVA_OPTS="-Xms512m -Xmx512m" \
	  -v compliance-esdata:/usr/share/elasticsearch/data \
	  docker.io/library/elasticsearch:8.19.9

down: ## Stop and remove app + ollama containers
	-podman rm -f $(APP) $(OLLAMA)

build: ## Build the app image
	podman build -t $(IMAGE) -f deploy/podman/Containerfile .

logs: ## Tail app logs
	podman logs -f $(APP)

ollama-pull: ## Pull LLM + embedding models into the Ollama container
	podman exec $(OLLAMA) ollama pull $(OLLAMA_MODEL)
	podman exec $(OLLAMA) ollama pull $(EMBED_MODEL)

# ---------- data ----------
indices: ## Create ES indices (runs inside the app container)
	podman exec $(APP) python scripts/create_indices.py

seed: ## Seed demo SOC2-style rules into ES
	podman exec $(APP) python scripts/seed_rules.py

fixtures: ## Generate sample policy PDFs locally (needs fpdf2)
	uv run --extra fixtures python scripts/make_fixtures.py

# ---------- tests ----------
test-unit: ## Unit tests (no external services)
	uv run --extra dev pytest tests/unit -q

test-integration: ## Integration tests (needs ES + Ollama running)
	COMPLIANCE_IT=1 uv run --extra dev pytest tests/integration -q

test: test-unit ## Default: unit tests

e2e: ## Full API e2e against running stack
	bash scripts/e2e_demo.sh

clean: ## Remove containers, volumes, generated fixtures/uploads
	-podman rm -f $(APP) $(OLLAMA) $(ES)
	-podman volume rm compliance-uploads compliance-ollama-models compliance-esdata
	rm -rf data/uploads fixtures/policies/*.pdf
