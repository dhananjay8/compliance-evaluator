"""Runtime context injected into nodes (services, settings).

Kept outside LangGraph state so the state stays serializable.
"""

from dataclasses import dataclass

from app.config import Settings
from app.documents.chunking.chunker import PolicyChunker
from app.documents.classification.classifier import DocumentClassifier
from app.documents.extraction.pdf import PdfExtractor
from app.elasticsearch.store import ElasticsearchStore
from app.embeddings.provider import EmbeddingProvider
from app.llm.provider import LLMProvider
from app.retrieval.retriever import EvidenceRetriever
from app.compliance.evaluator import ComplianceEvaluator
from app.compliance.gap import GapAnalyzer
from app.storage.local import LocalStorage


@dataclass
class GraphContext:
    settings: Settings
    store: ElasticsearchStore
    storage: LocalStorage
    extractor: PdfExtractor
    classifier: DocumentClassifier
    chunker: PolicyChunker
    embedder: EmbeddingProvider
    retriever: EvidenceRetriever
    evaluator: ComplianceEvaluator
    gap_analyzer: GapAnalyzer
    llm: LLMProvider
