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
    ("instruction_override", re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?|prompts?)", re.I
    )),
    ("instruction_disregard", re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I)),
    ("role_hijack", re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I)),
    ("system_role_label", re.compile(r"system\s*:\s*", re.I)),
    ("assistant_role_label", re.compile(r"\bassistant\s*:\s*", re.I)),
    ("forced_compliance", re.compile(
        r"mark\s+(this|all|every)\s+(rule|criterion|control)s?\s+(as\s+)?compliant", re.I
    )),
    ("forced_output", re.compile(r"(say|respond|answer|output|return)\s+[\"']?compliant[\"']?", re.I)),
    ("xml_instruction_tag", re.compile(r"<\s*/?\s*(system|instruction|prompt)\s*>", re.I)),
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
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > settings.max_docx_archive_members:
                    raise UploadValidationError(
                        f"DOCX archive contains too many entries ({len(members)})"
                    )
                expanded_size = sum(member.file_size for member in members)
                max_expanded_size = settings.max_docx_uncompressed_size_mb * 1024 * 1024
                if expanded_size > max_expanded_size:
                    raise UploadValidationError(
                        "DOCX expands beyond the "
                        f"{settings.max_docx_uncompressed_size_mb} MB safety limit"
                    )
                if "word/document.xml" not in archive.namelist():
                    raise UploadValidationError("File is not a valid .docx (missing document.xml)")
        except (zipfile.BadZipFile, OSError) as exc:
            raise UploadValidationError("File is not a valid .docx (invalid zip container)") from exc
    return safe_name


def detect_prompt_injection(text: str) -> list[str]:
    """Return safe pattern identifiers, never excerpts from sensitive policy text."""
    findings: list[str] = []
    for name, pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(name)
    return findings
