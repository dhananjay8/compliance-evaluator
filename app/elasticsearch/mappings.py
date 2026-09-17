"""Explicit Elasticsearch index definitions.

All index bodies are pure functions of settings so embedding dims stay configurable.
"""

from app.config import Settings


def policy_documents_mapping() -> dict:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "document_id": {"type": "keyword"},
                "assessment_id": {"type": "keyword"},
                "customer_id": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "content_type": {"type": "keyword"},
                "size_bytes": {"type": "long"},
                "sha256": {"type": "keyword"},
                "storage_path": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "classification_source": {"type": "keyword"},
                "classification_confidence": {"type": "float"},
                "page_count": {"type": "integer"},
                "uploaded_at": {"type": "date"},
            }
        },
    }


def policy_chunks_mapping(settings: Settings) -> dict:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "mappings": {
            "properties": {
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "assessment_id": {"type": "keyword"},
                "customer_id": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "policy_category": {"type": "keyword"},
                "page_number": {"type": "integer"},
                "page_end": {"type": "integer"},
                "section": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "chunk_index": {"type": "integer"},
                "text": {"type": "text"},
                "content_hash": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.embedding_dims,
                    "index": True,
                    "similarity": "cosine",
                },
                "metadata": {"type": "object", "enabled": True},
                "indexed_at": {"type": "date"},
            }
        },
    }


def compliance_rules_mapping() -> dict:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "rule_id": {"type": "keyword"},
                "framework": {"type": "keyword"},
                "trust_service_category": {"type": "keyword"},
                "criterion": {"type": "keyword"},
                "sub_attribute": {"type": "keyword"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
                "description": {"type": "text"},
                "requirement": {"type": "text"},
                "expected_evidence": {"type": "keyword"},
                "keywords": {"type": "keyword"},
                "severity": {"type": "keyword"},
                "version": {"type": "keyword"},
                "active": {"type": "boolean"},
                "document_types": {"type": "keyword"},
                "retrieval_text": {"type": "text"},
            }
        },
    }


def assessments_mapping() -> dict:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "assessment_id": {"type": "keyword"},
                "name": {"type": "keyword"},
                "framework": {"type": "keyword"},
                "customer_id": {"type": "keyword"},
                "status": {"type": "keyword"},
                "document_ids": {"type": "keyword"},
                "rule_ids": {"type": "keyword"},
                "error": {"type": "text"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "completed_at": {"type": "date"},
            }
        },
    }


def assessment_results_mapping() -> dict:
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "assessment_id": {"type": "keyword"},
                "rule_id": {"type": "keyword"},
                "criterion": {"type": "keyword"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
                "verdict": {"type": "keyword"},
                "evidence_status": {"type": "keyword"},
                "confidence": {"type": "float"},
                "reasoning": {"type": "text"},
                "citations": {
                    "type": "nested",
                    "properties": {
                        "filename": {"type": "keyword"},
                        "page_number": {"type": "integer"},
                        "section": {"type": "keyword"},
                        "quote": {"type": "text"},
                        "chunk_id": {"type": "keyword"},
                    },
                },
                "missing_requirements": {"type": "text"},
                "contradictions": {"type": "text"},
                "gap_analysis": {
                    "properties": {
                        "rule_id": {"type": "keyword"},
                        "criterion": {"type": "keyword"},
                        "gap": {"type": "text"},
                        "missing_requirement": {"type": "text"},
                        "recommendation": {"type": "text"},
                        "expected_evidence": {"type": "keyword"},
                        "severity": {"type": "keyword"},
                    }
                },
                "evaluated_at": {"type": "date"},
            }
        },
    }


def all_index_definitions(settings: Settings) -> dict[str, dict]:
    """logical name -> index body"""
    return {
        settings.idx_policy_documents: policy_documents_mapping(),
        settings.idx_policy_chunks: policy_chunks_mapping(settings),
        settings.idx_compliance_rules: compliance_rules_mapping(),
        settings.idx_assessments: assessments_mapping(),
        settings.idx_assessment_results: assessment_results_mapping(),
    }
