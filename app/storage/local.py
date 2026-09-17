"""Safe local filesystem storage for uploaded PDFs.

Layout: {upload_dir}/{assessment_id}/{document_id}.pdf
Path traversal is impossible by construction: filenames are never used as paths.
"""

from pathlib import Path

import aiofiles

from app.config import Settings, get_settings
from app.core.errors import StorageError


class LocalStorage:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.root = Path(self.settings.upload_dir).resolve()

    def _safe_path(self, assessment_id: str, document_id: str) -> Path:
        # IDs are UUIDs generated server-side; still sanitize defensively.
        for value in (assessment_id, document_id):
            if "/" in value or "\\" in value or ".." in value:
                raise StorageError(f"Unsafe identifier rejected: {value!r}")
        path = (self.root / assessment_id / f"{document_id}.pdf").resolve()
        if not str(path).startswith(str(self.root)):
            raise StorageError("Resolved path escapes upload root")
        return path

    async def save(self, assessment_id: str, document_id: str, data: bytes) -> Path:
        path = self._safe_path(assessment_id, document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as fh:
            await fh.write(data)
        return path

    def path_for(self, assessment_id: str, document_id: str) -> Path:
        return self._safe_path(assessment_id, document_id)

    async def read(self, assessment_id: str, document_id: str) -> bytes:
        path = self._safe_path(assessment_id, document_id)
        if not path.exists():
            raise StorageError(f"Stored document missing: {document_id}")
        async with aiofiles.open(path, "rb") as fh:
            return await fh.read()
