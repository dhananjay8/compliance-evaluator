"""Retrieved evidence schema."""

from pydantic import BaseModel


class RetrievedEvidence(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    document_type: str | None = None
    page_number: int
    section: str | None = None
    text: str
    similarity_score: float
    retrieval_method: str = "knn"  # "knn" | "bm25" | "hybrid"
