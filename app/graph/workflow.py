"""LangGraph workflow assembly.

Graph:

  ingest_documents → extract_document_text → classify_documents →
  normalize_documents → chunk_documents → generate_embeddings →
  index_policy_chunks → load_compliance_framework →
  retrieve_policy_evidence → evaluate_evidence ─┐
       ↑________________________________________| (loop while pending_rule_ids)
       → aggregate_criterion_results → generate_gap_analysis →
       generate_final_report → persist_assessment → END

Fatal errors short-circuit to persist_assessment so status is always recorded.
"""

from langgraph.graph import END, StateGraph

from app.graph.context import GraphContext
from app.graph.nodes.chunk_index import (
    make_chunk_documents,
    make_generate_embeddings,
    make_index_policy_chunks,
)
from app.graph.nodes.evaluate import (
    make_evaluate_evidence,
    make_load_compliance_framework,
    make_retrieve_policy_evidence,
)
from app.graph.nodes.ingest import (
    make_classify_documents,
    make_extract_document_text,
    make_ingest_documents,
    make_normalize_documents,
)
from app.graph.nodes.report import (
    make_aggregate_criterion_results,
    make_generate_final_report,
    make_generate_gap_analysis,
    make_persist_assessment,
)
from app.graph.state import ComplianceState


def _fatal_or_next(next_node: str):
    """Route to persist_assessment when a node set fatal_error."""
    def route(state: ComplianceState) -> str:
        return "persist_assessment" if state.get("fatal_error") else next_node
    return route


def _rules_remaining(state: ComplianceState) -> str:
    if state.get("fatal_error"):
        return "persist_assessment"
    return "retrieve_policy_evidence" if state.get("pending_rule_ids") else "aggregate_criterion_results"


def build_graph(ctx: GraphContext) -> StateGraph:
    graph = StateGraph(ComplianceState)

    graph.add_node("ingest_documents", make_ingest_documents(ctx))
    graph.add_node("extract_document_text", make_extract_document_text(ctx))
    graph.add_node("classify_documents", make_classify_documents(ctx))
    graph.add_node("normalize_documents", make_normalize_documents(ctx))
    graph.add_node("chunk_documents", make_chunk_documents(ctx))
    graph.add_node("generate_embeddings", make_generate_embeddings(ctx))
    graph.add_node("index_policy_chunks", make_index_policy_chunks(ctx))
    graph.add_node("load_compliance_framework", make_load_compliance_framework(ctx))
    graph.add_node("retrieve_policy_evidence", make_retrieve_policy_evidence(ctx))
    graph.add_node("evaluate_evidence", make_evaluate_evidence(ctx))
    graph.add_node("aggregate_criterion_results", make_aggregate_criterion_results(ctx))
    graph.add_node("generate_gap_analysis", make_generate_gap_analysis(ctx))
    graph.add_node("generate_final_report", make_generate_final_report(ctx))
    graph.add_node("persist_assessment", make_persist_assessment(ctx))

    graph.set_entry_point("ingest_documents")

    graph.add_conditional_edges("ingest_documents", _fatal_or_next("extract_document_text"))
    graph.add_conditional_edges("extract_document_text", _fatal_or_next("classify_documents"))
    graph.add_edge("classify_documents", "normalize_documents")
    graph.add_conditional_edges("normalize_documents", _fatal_or_next("chunk_documents"))
    graph.add_conditional_edges("chunk_documents", _fatal_or_next("generate_embeddings"))
    graph.add_conditional_edges("generate_embeddings", _fatal_or_next("index_policy_chunks"))
    graph.add_conditional_edges("index_policy_chunks", _fatal_or_next("load_compliance_framework"))
    graph.add_conditional_edges("load_compliance_framework", _fatal_or_next("retrieve_policy_evidence"))

    graph.add_edge("retrieve_policy_evidence", "evaluate_evidence")
    graph.add_conditional_edges("evaluate_evidence", _rules_remaining)

    graph.add_edge("aggregate_criterion_results", "generate_gap_analysis")
    graph.add_edge("generate_gap_analysis", "generate_final_report")
    graph.add_edge("generate_final_report", "persist_assessment")
    graph.add_edge("persist_assessment", END)

    return graph.compile()
