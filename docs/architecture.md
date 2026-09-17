# Architecture — Compliance Evaluator

## Overview

The platform evaluates uploaded policy PDFs against a matrix of atomic
compliance rules. It is a single FastAPI service orchestrating a LangGraph
state machine; Elasticsearch is the system of record for documents, chunks,
rules, assessments, and results; Ollama provides the LLM and (by default)
embeddings.

## Pipeline (LangGraph)

```
ingest_documents ─► extract_text ─► classify ─► normalize
      ─► chunk ─► embed ─► index_chunks
      ─► load_rules ─► ┌─ retrieve_evidence ─► evaluate_rule ─┐ (loop per rule)
      ─► aggregate ─► gap_analysis ─► build_report ─► persist
```

- **Typed state** (`ComplianceState` TypedDict) carries documents, extracted
  pages, classified docs, chunks, rules, evidence, evaluations, results,
  errors, and timings between nodes.
- **Conditional edges**: any node can set `fatal_error` → route straight to
  `persist` (marks assessment FAILED). The rule loop repeats
  `retrieve_evidence → evaluate_rule` until `pending_rules` is empty.
- **Error capture**: non-fatal per-document/per-rule failures append to
  `state.errors` and the pipeline continues → assessment ends `PARTIAL`.

## Components

### Ingestion
- `storage/local.py` — PDFs stored under `UPLOAD_DIR/{assessment_id}/{doc_id}.pdf`;
  path-traversal-safe, sha256 recorded.
- `security/validation.py` — extension + `%PDF` magic + size limits;
  filename sanitization; injection-pattern scanner.
- `documents/extraction/pdf.py` — PyMuPDF page-level text + bold-heading
  detection; whitespace-normalized but verbatim-preserving (citations must
  remain exact substrings).
- `documents/classification/classifier.py` — three modes: explicit routing
  (upload-time `document_type`), heuristic (filename + keyword scoring),
  LLM fallback (JSON-only classification below confidence threshold).
- `documents/chunking/chunker.py` — paragraph-aware chunking, never merges
  across pages; carries `document_id/filename/page/section/document_type`
  into every chunk; `content_hash` enables idempotent re-indexing.

### Retrieval
- `embeddings/` — `EmbeddingProvider` protocol; Ollama (`nomic-embed-text`,
  768-d) and optional `sentence-transformers` implementations.
- `retrieval/retriever.py` — hybrid: ES kNN on `dense_vector` + BM25
  `match` on text/section, filtered by `assessment_id` (+ optional
  `document_type`), `min_similarity` floor, dedup, capped at
  `MAX_EVIDENCE_CHUNKS`.

### Evaluation
- `compliance/prompts.py` — system prompt fixes the audit contract; policy
  text is embedded only inside `BEGIN/END_POLICY_EVIDENCE` delimiters marked
  UNTRUSTED. JSON-schema-constrained output via Ollama `format` parameter.
- `compliance/evaluator.py` — builds prompt, parses + validates structured
  output, retries once on malformed JSON, then **enforces semantics in
  code**: `PARTIAL`/`ABSENT`/`CONTRADICTORY` ⇒ `NOT_COMPLIANT` regardless of
  model output; `COMPLIANT` requires ≥1 valid citation.
- `compliance/citation.py` — each citation must be a verbatim
  (whitespace-tolerant) substring of a retrieved chunk's stored text,
  resolvable by `chunk_id` or `filename+page`. Invalid citations are
  rejected; a `COMPLIANT` verdict with zero valid citations is downgraded.
- `compliance/gap.py` — for each `NOT_COMPLIANT` rule, LLM produces
  gap / missing requirement / recommendation / expected evidence / severity;
  deterministic fallback if the model fails.
- `compliance/aggregation.py` — per-criterion results + assessment summary
  (counts, compliance %, evidence-status breakdown).

### Persistence (Elasticsearch)
Five indices under a configurable prefix:

| Index | Contents |
|---|---|
| `policy_documents` | upload metadata, classification, status |
| `policy_chunks` | chunk text + metadata + `dense_vector` embedding |
| `compliance_rules` | atomic rules (framework, criterion, requirement, keywords) |
| `assessments` | lifecycle status, summary, timings, errors |
| `assessment_results` | per-rule verdict, evidence, citations, gap |

## Prompt-injection defense (defense in depth)

1. **Architectural isolation** — policy text never enters the system prompt;
   it is data inside delimiters.
2. **Instruction hierarchy** — system prompt explicitly forbids following
   instructions found inside evidence.
3. **Heuristic detection** — uploads scanned for injection patterns
   (`ignore previous instructions`, role hijacking, etc.); hits are logged
   and flagged on the document record.
4. **Semantic enforcement** — verdict rules are applied in Python after the
   LLM responds; the model cannot self-certify compliance.
5. **Citation grounding** — fabricated quotes/documents/pages are rejected
   by verbatim substring verification.

## Verdict semantics

| Evidence status | Verdict |
|---|---|
| `EXPLICIT` + valid citation | `COMPLIANT` |
| `EXPLICIT` w/o valid citation | `NOT_COMPLIANT` (downgraded) |
| `PARTIAL`, `ABSENT`, `CONTRADICTORY` | `NOT_COMPLIANT` |

## Configuration & extensibility

- All settings via env (`app/config/settings.py`, pydantic-settings).
- LLM/embedding providers behind protocols — swap Ollama for another local
  server by implementing `LLMProvider`/`EmbeddingProvider`.
- Rules are data: seed from `fixtures/rules/soc2_demo_rules.json` or import
  from the library-sync composite index via
  `scripts/import_rules_from_library.py`.
