"""Upload validation + prompt-injection heuristics.

Policy documents are untrusted input. Injection detection is observational
(flag + log) — the architectural defense is prompt isolation in the evaluator.
"""

import io
import re
import unicodedata
import zipfile

from app.config import Settings, get_settings
from app.core.errors import UploadValidationError

_MAGIC_BY_EXT: dict[str, bytes] = {
    ".pdf": b"%PDF-",
    ".docx": b"PK\x03\x04",  # zip container
}

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?|prompts?)", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"\bassistant\s*:\s*", re.I),
    re.compile(r"mark\s+(this|all|every)\s+(rule|criterion|control)s?\s+(as\s+)?compliant", re.I),
    re.compile(r"(say|respond|answer|output|return)\s+[\"']?compliant[\"']?", re.I),
    re.compile(r"<\s*/?\s*(system|instruction|prompt)\s*>", re.I),
]


def sanitize_filename(filename: str) -> str:
    """Strip path components and control chars; keep a readable name."""
    name = filename.replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if not name or name in (".", ".."):
        raise UploadValidationError("Invalid filename")
    return name[:255]


def validate_upload(filename: str, data: bytes, settings: Settings | None = None) -> str:
    """Validate extension, size, and magic bytes. Returns sanitized filename."""
    settings = settings or get_settings()
    safe_name = sanitize_filename(filename)

    ext = "." + safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if ext not in settings.allowed_extensions:
        raise UploadValidationError(
            f"Unsupported file type {ext!r}; allowed: {sorted(settings.allowed_extensions)}"
        )
    if not data:
        raise UploadValidationError("Empty file")
    if len(data) > settings.max_file_size_bytes:
        raise UploadValidationError(
            f"File exceeds {settings.max_file_size_mb} MB limit ({len(data)} bytes)"
        )
    magic = _MAGIC_BY_EXT.get(ext)
    if magic and not data.startswith(magic):
        raise UploadValidationError(f"File is not a valid {ext} (bad magic bytes)")
    if ext == ".docx" and not zipfile.is_zipfile(io.BytesIO(data)):
        raise UploadValidationError("File is not a valid .docx (not a zip container)")
    return safe_name


def detect_prompt_injection(text: str) -> list[str]:
    """Return matched injection-pattern descriptions for observability."""
    findings: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(match.group(0)[:80])
    return findings
