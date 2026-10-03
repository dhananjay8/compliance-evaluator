"""Builds the GraphContext (service wiring) and runs the compiled graph."""

from app.compliance.evaluator import ComplianceEvaluator
from app.compliance.gap import GapAnalyzer
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.documents.chunking.chunker import PolicyChunker
from app.documents.classification.classifier import DocumentClassifier
from app.documents.extraction import DocumentExtractor
from app.elasticsearch.client import get_es_client
from app.elasticsearch.store import ElasticsearchStore
from app.embeddings.provider import get_embedding_provider
from app.graph.context import GraphContext
from app.graph.state import ComplianceState
from app.graph.workflow import build_graph
from app.llm.provider import get_llm_provider
from app.retrieval.retriever import EvidenceRetriever
from app.storage.local import LocalStorage

log = get_logger(__name__)


def build_context(settings: Settings | None = None) -> GraphContext:
    settings = settings or get_settings()
    store = ElasticsearchStore(get_es_client(settings), settings)
    llm = get_llm_provider(settings)
    embedder = get_embedding_provider(settings)
    return GraphContext(
        settings=settings,
        store=store,
        storage=LocalStorage(settings),
        extractor=DocumentExtractor(),
        classifier=DocumentClassifier(settings, llm=llm),
        chunker=PolicyChunker(settings),
        embedder=embedder,
        retriever=EvidenceRetriever(store, embedder, settings),
        evaluator=ComplianceEvaluator(llm, settings),
        gap_analyzer=GapAnalyzer(llm, settings),
        llm=llm,
    )


async def run_assessment(ctx: GraphContext, initial_state: ComplianceState) -> ComplianceState:
    graph = build_graph(ctx)
    log.info("assessment_graph_start", assessment_id=initial_state.get("assessment_id"))
    final = await graph.ainvoke(initial_state)
    log.info(
        "assessment_graph_done",
        assessment_id=initial_state.get("assessment_id"),
        errors=len(final.get("errors", [])),
        fatal=final.get("fatal_error"),
    )
    return final
