"""Central configuration. Every operational value is env-driven."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8090, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")

    # --- Elasticsearch ---
    elasticsearch_url: str = Field(default="http://localhost:9200", alias="ELASTICSEARCH_URL")
    elasticsearch_username: str | None = Field(default=None, alias="ELASTICSEARCH_USERNAME")
    elasticsearch_password: str | None = Field(default=None, alias="ELASTICSEARCH_PASSWORD")
    es_index_prefix: str = Field(default="", alias="ES_INDEX_PREFIX")
    es_request_timeout: int = Field(default=30, alias="ES_REQUEST_TIMEOUT")

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
    llm_timeout_seconds: int = Field(default=180, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_num_ctx: int = Field(default=8192, alias="LLM_NUM_CTX")

    # --- Embeddings ---
    # provider: "ollama" | "sentence_transformers"
    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="nomic-embed-text", alias="EMBEDDING_MODEL")
    embedding_dims: int = Field(default=768, alias="EMBEDDING_DIMS")
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE")
    embedding_timeout_seconds: int = Field(default=120, alias="EMBEDDING_TIMEOUT_SECONDS")

    # --- Chunking ---
    chunk_size: int = Field(default=1000, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=150, alias="CHUNK_OVERLAP")

    # --- Retrieval ---
    top_k: int = Field(default=8, alias="TOP_K")
    min_similarity: float = Field(default=0.5, alias="MIN_SIMILARITY")
    max_evidence_chunks: int = Field(default=6, alias="MAX_EVIDENCE_CHUNKS")
    retrieval_bm25_candidates: int = Field(default=8, alias="RETRIEVAL_BM25_CANDIDATES")

    # --- Uploads / security ---
    max_file_size_mb: int = Field(default=25, alias="MAX_FILE_SIZE_MB")
    max_files_per_assessment: int = Field(default=20, alias="MAX_FILES_PER_ASSESSMENT")
    upload_dir: Path = Field(default=Path("/data/uploads"), alias="UPLOAD_DIR")
    allowed_extensions: tuple[str, ...] = (".pdf", ".docx")

    # --- Evaluation ---
    evaluation_max_rule_workers: int = Field(default=1, alias="EVALUATION_MAX_RULE_WORKERS")
    citation_max_retries: int = Field(default=1, alias="CITATION_MAX_RETRIES")
    default_framework: str = Field(default="SOC2", alias="DEFAULT_FRAMEWORK")

    # --- Classification ---
    classifier_llm_enabled: bool = Field(default=True, alias="CLASSIFIER_LLM_ENABLED")
    classifier_llm_threshold: float = Field(default=0.55, alias="CLASSIFIER_LLM_THRESHOLD")

    def index_name(self, logical: str) -> str:
        return f"{self.es_index_prefix}{logical}" if self.es_index_prefix else logical

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
