# Implementation Plan — Compliance Policy Evaluation Platform

## Context

Standalone Python service `compliance-evaluator/` — a local-first compliance
policy evaluation platform.

Infrastructure notes:

- Elasticsearch 8.19.9 runs as a Podman container (`compliance-es`) on the
  `compliance-net` bridge network, published on host `localhost:9200`.
- No `docker-compose`/`podman-compose` provider required → primary dev path is
  plain `podman` via Makefile/scripts; `deploy/podman/compose.yaml` is provided
  for users who have a compose provider.
- Keep the stack light (no torch in the default path).
- The synthetic rule dataset is modeled on generic control-library structures
  (SOC2/ISO-style framework categories, evidence catalog). No copyrighted
  SOC2 text copied.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language / runtime | Python 3.12 (container `python:3.12-slim`) | Spec requirement |
| Orchestration | `langgraph` only (no langchain) | Typed explicit state machine; fewer deps |
| API | FastAPI + uvicorn, `BackgroundTasks` for async eval | Spec; extensible to workers later |
| Vector store | Elasticsearch 8 `dense_vector` + `knn` search, BM25 hybrid | Spec |
| Embeddings | `EmbeddingProvider` abstraction; default `OllamaEmbeddingProvider` (`nomic-embed-text`, 768d); optional `SentenceTransformerProvider` (`BAAI/bge-small-en-v1.5`, 384d) | Keeps app image slim (no torch); spec-compliant configurability |
| LLM | `LLMProvider` abstraction; `OllamaLLMProvider` via `/api/chat` with JSON-schema `format` | Local, open-source, swappable via env |
| Default model | `qwen2.5:3b` (strong JSON adherence, ~2 GB); `llama3.1:8b` documented | RAM budget |
| PDF parsing | PyMuPDF (`pymupdf`) | Spec; page/section/offset fidelity |
| ES | Default `ELASTICSEARCH_URL=http://compliance-es:9200` (in-net) / `http://localhost:9200` (host); bundled `compliance-es` container | Self-contained |
| Rules | Synthetic SOC2-style atomic rules JSON (~48 rules, 15 domains) seeded into `compliance_rules`; optional importer from `audit_library_control_composite` | Licensing-safe; integrates with existing ES data |
| State persistence | `assessments` + `assessment_results` ES indices; PDFs on local disk (`/data` volume) | No extra infra |

## Module layout

```
app/
  main.py                  FastAPI app factory, lifespan (index bootstrap)
  config/settings.py       pydantic-settings, all env-driven
  core/                    errors, hashing (sha256), logging (structlog)
  schemas/                 pydantic models: document, chunk, rule, evidence,
                           evaluation, gap, results, api payloads
  graph/
    state.py               ComplianceState TypedDict
    workflow.py            build_graph() — nodes + conditional edges
    nodes/                 one module per stage
  documents/
    extraction/pdf.py      PyMuPDF page/section extraction
    classification/        explicit routing + heuristic + LLM classifier
    chunking/chunker.py    page→section→paragraph aware chunker
  embeddings/              provider.py, ollama.py, sentence_transformer.py
  retrieval/retriever.py   hybrid kNN + BM25 + metadata filters
  compliance/
    prompts.py             evaluator + gap prompts (injection-hardened)
    evaluator.py           structured eval loop, retry, parse
    citation.py            verbatim quote validation vs chunk text
    gap.py                 gap analysis generation
    aggregation.py         verdict aggregation + summary
  elasticsearch/
    client.py              async ES client factory
    mappings.py            index definitions (dims from settings)
    store.py               index/CRUD/bulk helpers
  llm/
    provider.py            LLMProvider protocol
    ollama.py              Ollama chat + JSON-schema structured output
  storage/local.py         safe temp/upload dir handling
  security/validation.py   file checks, size limits, injection heuristics
tests/{unit,integration,e2e}
fixtures/{policies,rules}
scripts/{create_indices,seed_rules,make_fixtures,import_rules_from_library,dev-up,dev-down}.py/.sh
deploy/podman/{Containerfile,compose.yaml}
docs/{architecture,api,local-development,implementation-plan}.md
Makefile, README.md, .env.example, pyproject.toml
```

## LangGraph flow

```
ingest_documents → extract_document_text → classify_documents →
normalize_documents → chunk_documents → generate_embeddings →
index_policy_chunks → load_compliance_framework →
retrieve_policy_evidence → evaluate_evidence ─┐
      ↑_______________________________________| (loop while rules remain)
      → aggregate_criterion_results → generate_gap_analysis →
      generate_final_report → persist_assessment → END
```

Per-rule loop uses a `pending_rule_ids` queue in state + conditional edge.
Citation validation runs inside `evaluate_evidence` (code-level, with one LLM retry,
then downgrade). Errors accumulate in `state["errors"]`; fatal errors route to
`persist_assessment` with status FAILED/PARTIAL.

## Build order

1. Foundations: pyproject, settings, logging, schemas, errors, hashing.
2. ES client + mappings + `create_indices` script.
3. PDF extraction → chunking → embeddings → indexing → retrieval.
4. Rule schema + seed dataset + `seed_rules`.
5. Classification (explicit + heuristic + LLM fallback).
6. LLM provider + evaluator + citation validation + gap analysis.
7. LangGraph state/workflow/nodes.
8. Assessment store + FastAPI routes.
9. Podman deploy assets + Makefile + scripts.
10. Fixture PDF generator (fpdf2) + sample policies incl. injection case.
11. Unit tests (mocked ES/LLM) → integration → e2e.
12. Local run: start `compliance-ollama`, pull models, build
    `compliance-evaluator` image, run on `compliance-net`, seed rules,
    run real assessment, verify.
13. README + architecture docs + final report.

## Validation plan

- `pytest tests/unit` — no external services.
- `pytest tests/integration` — requires ES + Ollama (env-gated, skipped otherwise).
- `scripts/e2e_demo.sh` — full API flow against running containers.
- Manual: upload 3 fixture PDFs → evaluate subset of rules → verify verdicts,
  citations, gap analysis, injection resistance.
