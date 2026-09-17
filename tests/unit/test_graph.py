"""LangGraph workflow test — full graph with fake ES/LLM/embedder services.

Verifies node wiring, the per-rule loop, verdict propagation, and persistence
calls without any external infrastructure.
"""

import json
import uuid
from pathlib import Path

import pytest

from app.graph.context import GraphContext
from app.graph.runner import run_assessment
from app.graph.state import ComplianceState
from app.schemas.document import DocumentRef
from app.schemas.evaluation import EvidenceStatus, Verdict
from app.schemas.rule import ComplianceRule


class FakeStore:
    def __init__(self, rules):
        self.rules = rules
        self.saved_results = []
        self.status_updates = []
        self.documents = []

    async def index_document(self, doc, **kw):
        self.documents.append(doc)

    async def update_document_classification(self, *a, **kw):
        pass

    async def existing_chunk_hashes(self, *a, **kw):
        return set()

    async def bulk_index_chunks(self, chunks, embeddings):
        return len(chunks)

    async def refresh(self, *a):
        pass

    async def load_rules(self, framework, rule_ids=None):
        if rule_ids:
            return [r for r in self.rules if r.rule_id in rule_ids]
        return self.rules

    async def save_result(self, assessment_id, result):
        self.saved_results.append(result)

    async def update_assessment_status(self, aid, status, error=None):
        self.status_updates.append((aid, status, error))


class FakeEmbedder:
    @property
    def model_name(self):
        return "fake-embed"

    @property
    def dims(self):
        return 4

    async def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    async def health(self):
        return True


class FakeRetriever:
    def __init__(self, evidence_map):
        self.evidence_map = evidence_map

    async def retrieve_for_rule(self, assessment_id, rule, **kw):
        return self.evidence_map.get(rule.rule_id, [])


class FakeLLM:
    def __init__(self, responses):
        self.responses = responses  # rule_id -> payload dict

    @property
    def model_name(self):
        return "fake-llm"

    async def complete(self, prompt, **kw):
        for rid, payload in self.responses.items():
            if rid in prompt:
                return json.dumps(payload)
        return json.dumps({
            "rule_id": "unknown", "verdict": "NOT_COMPLIANT",
            "evidence_status": "ABSENT", "confidence": 0.5,
            "reasoning": "default", "citations": [],
        })


class FakeGapLLM(FakeLLM):
    async def complete(self, prompt, **kw):
        if "AUDIT OUTCOME" in prompt:
            return json.dumps({
                "gap": "The submitted policy corpus does not demonstrate the requirement.",
                "missing_requirement": "defined cadence",
                "recommendation": "Add explicit review cadence language.",
                "expected_evidence": ["review procedure"],
                "severity": "HIGH",
            })
        return await super().complete(prompt, **kw)


@pytest.mark.asyncio
async def test_full_graph_two_rules(settings, pdf_factory, tmp_path):
    from app.compliance.evaluator import ComplianceEvaluator
    from app.compliance.gap import GapAnalyzer
    from app.documents.chunking.chunker import PolicyChunker
    from app.documents.classification.classifier import DocumentClassifier
    from app.documents.extraction.pdf import PdfExtractor
    from app.schemas.evidence import RetrievedEvidence
    from app.storage.local import LocalStorage

    settings.upload_dir = tmp_path / "uploads"
    quote = "Privileged user access is reviewed every 90 days by the security team."
    pdf = pdf_factory("access.pdf", "Access Control Policy",
                      [("Privileged Access", [quote])])
    doc_id = str(uuid.uuid4())
    storage = LocalStorage(settings)
    await storage.save("a1", doc_id, pdf.read_bytes())

    rules = [
        ComplianceRule(rule_id="SOC2-CC6.1-004", criterion="CC6.1",
                       title="Periodic access reviews", description="d",
                       requirement="defined periodic access review cadence"),
        ComplianceRule(rule_id="SOC2-CC9.1-001", criterion="CC9.1",
                       title="Vendor risk assessment", description="d",
                       requirement="vendor due diligence before engagement"),
    ]
    evidence_map = {
        "SOC2-CC6.1-004": [RetrievedEvidence(
            chunk_id="c1", document_id=doc_id, filename="access.pdf",
            page_number=1, section="Privileged Access", text=quote,
            similarity_score=0.9,
        )],
        "SOC2-CC9.1-001": [],  # absent → NOT_COMPLIANT without LLM call
    }
    llm = FakeGapLLM({
        "SOC2-CC6.1-004": {
            "rule_id": "SOC2-CC6.1-004", "verdict": "COMPLIANT",
            "evidence_status": "EXPLICIT", "confidence": 0.95,
            "reasoning": "explicit 90-day cadence",
            "evidence": [quote], "missing_requirements": [],
            "contradictions": [],
            "citations": [{"filename": "access.pdf", "page_number": 1,
                           "quote": quote, "chunk_id": "c1"}],
            "recommended_gap": None,
        },
    })

    store = FakeStore(rules)
    ctx = GraphContext(
        settings=settings, store=store, storage=storage,
        extractor=PdfExtractor(),
        classifier=DocumentClassifier(settings, llm=None),
        chunker=PolicyChunker(settings),
        embedder=FakeEmbedder(),
        retriever=FakeRetriever(evidence_map),
        evaluator=ComplianceEvaluator(llm, settings),
        gap_analyzer=GapAnalyzer(llm, settings),
        llm=llm,
    )

    state: ComplianceState = {
        "assessment_id": "a1",
        "framework": "SOC2",
        "documents": [DocumentRef(
            document_id=doc_id, assessment_id="a1", filename="access.pdf",
            size_bytes=pdf.stat().st_size, sha256="x" * 64,
            storage_path=str(pdf),
        )],
        "errors": [],
        "timings": {},
    }
    final = await run_assessment(ctx, state)

    assert final.get("fatal_error") is None
    results = {r.rule_id: r for r in final["final_results"]}
    assert results["SOC2-CC6.1-004"].verdict == Verdict.COMPLIANT
    assert results["SOC2-CC6.1-004"].citations[0].quote == quote
    assert results["SOC2-CC9.1-001"].verdict == Verdict.NOT_COMPLIANT
    assert results["SOC2-CC9.1-001"].evidence_status == EvidenceStatus.ABSENT
    assert results["SOC2-CC9.1-001"].gap_analysis is not None
    assert "does not demonstrate" in results["SOC2-CC9.1-001"].gap_analysis.gap
    assert final["summary"].compliant == 1
    assert len(store.saved_results) == 2
    assert store.status_updates[-1][1].value == "COMPLETED"
