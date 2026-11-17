"""Central configuration. Every operational value is env-driven."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8090, alias="APP_PORT", gt=0, le=65535)
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")

    # --- Elasticsearch ---
    elasticsearch_url: str = Field(default="http://localhost:9200", alias="ELASTICSEARCH_URL")
    elasticsearch_username: str | None = Field(default=None, alias="ELASTICSEARCH_USERNAME")
    elasticsearch_password: str | None = Field(default=None, alias="ELASTICSEARCH_PASSWORD")
    es_index_prefix: str = Field(default="", alias="ES_INDEX_PREFIX")
    es_request_timeout: int = Field(default=30, alias="ES_REQUEST_TIMEOUT", gt=0)

    # Index names (prefixed at runtime)
    idx_policy_documents: str = "policy_documents"
    idx_policy_chunks: str = "policy_chunks"
    idx_compliance_rules: str = "compliance_rules"
    idx_assessments: str = "assessments"
    idx_assessment_results: str = "assessment_results"

    # --- Ollama / LLM ---
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="qwen2.5:3b", alias="OLLAMA_MODEL")
    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    llm_timeout_seconds: int = Field(default=180, alias="LLM_TIMEOUT_SECONDS", gt=0)
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES", ge=0)
    llm_num_ctx: int = Field(default=8192, alias="LLM_NUM_CTX", gt=0)

    # --- Embeddings ---
    # provider: "ollama" | "sentence_transformers"
    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="nomic-embed-text", alias="EMBEDDING_MODEL")
    embedding_dims: int = Field(default=768, alias="EMBEDDING_DIMS", gt=0)
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE", gt=0)
    embedding_timeout_seconds: int = Field(
        default=120, alias="EMBEDDING_TIMEOUT_SECONDS", gt=0
    )
    embedding_max_retries: int = Field(default=2, alias="EMBEDDING_MAX_RETRIES", ge=0)

    # --- Chunking ---
    chunk_size: int = Field(default=1000, alias="CHUNK_SIZE", gt=0)
    chunk_overlap: int = Field(default=150, alias="CHUNK_OVERLAP", ge=0)

    # --- Retrieval ---
    top_k: int = Field(default=8, alias="TOP_K", gt=0)
    min_similarity: float = Field(default=0.5, alias="MIN_SIMILARITY", ge=0, le=1)
    max_evidence_chunks: int = Field(default=6, alias="MAX_EVIDENCE_CHUNKS", gt=0)
    retrieval_bm25_candidates: int = Field(
        default=8, alias="RETRIEVAL_BM25_CANDIDATES", gt=0
    )

    # --- Uploads / security ---
    max_file_size_mb: int = Field(default=25, alias="MAX_FILE_SIZE_MB", gt=0)
    max_files_per_assessment: int = Field(
        default=20, alias="MAX_FILES_PER_ASSESSMENT", gt=0
    )
    max_docx_uncompressed_size_mb: int = Field(
        default=100, alias="MAX_DOCX_UNCOMPRESSED_SIZE_MB", gt=0
    )
    max_docx_archive_members: int = Field(
        default=5000, alias="MAX_DOCX_ARCHIVE_MEMBERS", gt=0
    )
    upload_dir: Path = Field(default=Path("/data/uploads"), alias="UPLOAD_DIR")
    allowed_extensions: tuple[str, ...] = (".pdf", ".docx")

    # --- Evaluation ---
    evaluation_max_rule_workers: int = Field(
        default=1, alias="EVALUATION_MAX_RULE_WORKERS", gt=0
    )
    citation_max_retries: int = Field(default=1, alias="CITATION_MAX_RETRIES", ge=0)
    assessment_shutdown_timeout_seconds: int = Field(
        default=30, alias="ASSESSMENT_SHUTDOWN_TIMEOUT_SECONDS", ge=0
    )
    default_framework: str = Field(default="SOC2", alias="DEFAULT_FRAMEWORK")

    # --- Classification ---
    classifier_llm_enabled: bool = Field(default=True, alias="CLASSIFIER_LLM_ENABLED")
    classifier_llm_threshold: float = Field(
        default=0.55, alias="CLASSIFIER_LLM_THRESHOLD", ge=0, le=1
    )

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self

    def index_name(self, logical: str) -> str:
        return f"{self.es_index_prefix}{logical}" if self.es_index_prefix else logical

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
