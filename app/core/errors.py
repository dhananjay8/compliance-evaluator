"""Typed application errors. Nodes catch these and record WorkflowError entries."""


class AppError(Exception):
    """Base error carrying a stable machine-readable code."""

    code: str = "app_error"
    retryable: bool = False

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class UploadValidationError(AppError):
    code = "upload_validation_error"


class DocumentExtractionError(AppError):
    code = "document_extraction_error"


class ClassificationError(AppError):
    code = "classification_error"


class EmbeddingError(AppError):
    code = "embedding_error"
    retryable = True


class ElasticsearchError(AppError):
    code = "elasticsearch_error"
    retryable = True


class LLMError(AppError):
    code = "llm_error"
    retryable = True


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMStructuredOutputError(LLMError):
    code = "llm_structured_output_error"
    retryable = False


class CitationValidationError(AppError):
    code = "citation_validation_error"
    retryable = False


class StorageError(AppError):
    code = "storage_error"


class RuleLoadError(AppError):
    code = "rule_load_error"
