# Compliance Evaluator

A locally runnable, open-source compliance-policy evaluation platform. Upload
policy PDFs, and the system evaluates them against a matrix of atomic
compliance rules (SOC 2–style) using hybrid Elasticsearch retrieval and a
locally hosted LLM — producing binary verdicts anchored to **verbatim PDF
citations**, with AI-generated gap analysis for every gap.

No OpenAI dependency. Everything runs on your machine: FastAPI + LangGraph +
Elasticsearch 8 + Ollama.

## Architecture

```
PDFs ──► extract (PyMuPDF) ──► classify ──► chunk ──► embed ──► ES index
                                                                 │
rules (ES) ──► per rule: hybrid retrieve (kNN+BM25) ──► LLM evaluate
                                                                 │
                        citation validation ◄── verbatim check ◄──┘
                                 │
                    aggregate ──► gap analysis ──► report ──► persist (ES)
```

Orchestrated as a LangGraph state machine with typed state, per-node error
capture, and a conditional per-rule evaluation loop. See
[docs/architecture.md](docs/architecture.md).

## Guarantees

- **Binary verdicts** — `COMPLIANT` / `NOT_COMPLIANT` only. Partial or
  ambiguous evidence is always `NOT_COMPLIANT`; the LLM cannot override this.
- **Verbatim citations** — a `COMPLIANT` verdict is only valid if every
  citation quote is an exact substring of the cited chunk's stored text.
  Paraphrased or fabricated citations are rejected and the verdict is
  downgraded.
- **Prompt-injection defense** — policy text is treated as untrusted data:
  wrapped in delimiters, never concatenated into instructions, and scanned
  for injection patterns. Verdict semantics are enforced in code, not by the
  model.
- **Full traceability** — every verdict links back to filename, page number,
  section, chunk id, and the verbatim quote.

## Quick start (Podman)

Prereqs: `podman`, `uv` (or Python 3.12+), and Elasticsearch — either run the
bundled container (`make es`) or point `ELASTICSEARCH_URL` at an existing
instance.

```bash
# 1. Start Elasticsearch + Ollama + app (builds image, joins compliance-net)
make es
make up

# 2. Pull models once (~2.3 GB)
make ollama-pull          # qwen2.5:3b + nomic-embed-text

# 3. Create ES indices + seed demo rules
make indices && make seed

# 4. Generate sample policy PDFs
make fixtures

# 5. Run the end-to-end demo
make e2e
```

App: http://localhost:8090 — docs at `/docs`, health at `/health`,
readiness at `/ready`.

### Without the Makefile

```bash
podman network create compliance-net 2>/dev/null || true
podman run -d --name compliance-es --network compliance-net -p 9200:9200 \
  -e discovery.type=single-node -e xpack.security.enabled=false \
  -v compliance-esdata:/usr/share/elasticsearch/data \
  docker.io/library/elasticsearch:8.19.9
podman run -d --name compliance-ollama --network compliance-net -p 11434:11434 \
  -v compliance-ollama-models:/root/.ollama docker.io/ollama/ollama:latest
podman exec compliance-ollama ollama pull qwen2.5:3b
podman exec compliance-ollama ollama pull nomic-embed-text

podman build -t localhost/compliance-evaluator:local -f deploy/podman/Containerfile .
podman run -d --name compliance-evaluator --network compliance-net -p 8090:8090 \
  -e ELASTICSEARCH_URL=http://compliance-es:9200 \
  -e OLLAMA_URL=http://compliance-ollama:11434 \
  -v compliance-uploads:/data/uploads \
  localhost/compliance-evaluator:local
```

`deploy/podman/compose.yaml` is also provided for `podman-compose` users.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/assessments` | Create assessment (`name`, `framework`) |
| `POST` | `/api/v1/assessments/{id}/documents` | Upload policy docs — PDF or DOCX (multipart; optional `document_type` per file for explicit routing) |
| `POST` | `/api/v1/assessments/{id}/evaluate` | Start evaluation (optional `rule_ids` subset) |
| `GET` | `/api/v1/assessments/{id}` | Status + summary |
| `GET` | `/api/v1/assessments/{id}/results` | All criterion results |
| `GET` | `/api/v1/assessments/{id}/results/{rule_id}` | Single result with citations + gap |
| `GET` | `/health` `/ready` | Liveness / readiness (ES, Ollama, embedder) |

```bash
curl -X POST localhost:8090/api/v1/assessments \
  -H 'Content-Type: application/json' -d '{"name":"demo","framework":"SOC2"}'

curl -X POST localhost:8090/api/v1/assessments/$AID/documents \
  -F 'files=@fixtures/policies/access_control_policy.pdf' \
  -F 'files=@fixtures/policies/incident_response_policy.pdf'

curl -X POST localhost:8090/api/v1/assessments/$AID/evaluate \
  -H 'Content-Type: application/json' -d '{}'
```

## Configuration

All settings via environment variables (see `.env.example`):

- `OLLAMA_MODEL` (default `qwen2.5:3b`), `OLLAMA_URL`
- `EMBEDDING_PROVIDER` = `ollama` | `sentence_transformers`,
  `EMBEDDING_MODEL` (default `nomic-embed-text`), `EMBEDDING_DIMS` (768)
- `CHUNK_SIZE` (1000), `CHUNK_OVERLAP` (150)
- `TOP_K` (8), `MIN_SIMILARITY` (0.5), `MAX_EVIDENCE_CHUNKS` (6)
- `MAX_FILE_SIZE_MB` (25), `MAX_FILES_PER_ASSESSMENT` (20)
- `CLASSIFIER_LLM_ENABLED`, `CLASSIFIER_LLM_THRESHOLD` (0.55)

## Testing

```bash
make test-unit          # 40 unit tests, no services needed
make test-integration   # needs ES + Ollama (COMPLIANCE_IT=1)
make e2e                # full API flow against running stack
```

Unit tests cover: PDF extraction fidelity, chunk boundaries/metadata,
explicit+heuristic+LLM classification, verbatim citation enforcement,
verdict semantics (partial→NOT_COMPLIANT, no-citation downgrade),
prompt-injection resistance, upload validation, aggregation, and the full
LangGraph workflow with fake services.

## Layout

```
app/
  api/            FastAPI routes, schemas, assessment service
  compliance/     evaluator, prompts, citation validation, gap, aggregation
  config/         pydantic-settings
  documents/      extraction (PyMuPDF), chunking, classification
  elasticsearch/  client, index mappings, store
  embeddings/     provider abstraction (ollama, sentence-transformers)
  graph/          LangGraph state, context, nodes, workflow, runner
  llm/            provider abstraction (ollama, JSON-schema output)
  retrieval/      hybrid kNN + BM25 evidence retriever
  schemas/        pydantic models (documents, rules, evidence, results)
  security/       upload validation, injection detection
  storage/        local filesystem PDF storage
deploy/podman/    Containerfile, compose.yaml
fixtures/         seed rules + generated sample policy PDFs
scripts/          create_indices, seed_rules, import_rules_from_library,
                  make_fixtures, e2e_demo
tests/            unit / integration / e2e
```
