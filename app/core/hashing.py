"""Content hashing for idempotent ingest/embedding."""

import hashlib
import re

_WS_RE = re.compile(r"\s+")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_for_hash(text: str) -> str:
    """Whitespace-insensitive normalization used for chunk content hashes."""
    return _WS_RE.sub(" ", text).strip().lower()


def chunk_content_hash(document_id: str, page_number: int, chunk_index: int, text: str) -> str:
    payload = f"{document_id}|{page_number}|{chunk_index}|{normalize_for_hash(text)}"
    return sha256_text(payload)
