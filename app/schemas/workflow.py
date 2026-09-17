"""Workflow-level error record."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class WorkflowError(BaseModel):
    node: str
    code: str
    message: str
    retryable: bool = False
    rule_id: str | None = None
    document_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
