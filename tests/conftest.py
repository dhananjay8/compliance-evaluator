import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        UPLOAD_DIR=tmp_path / "uploads",
        CHUNK_SIZE=400,
        CHUNK_OVERLAP=50,
        TOP_K=5,
        MIN_SIMILARITY=0.3,
        MAX_EVIDENCE_CHUNKS=4,
        EMBEDDING_DIMS=8,
    )


def make_pdf(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> Path:
    """Minimal real PDF via fpdf2 (dev extra)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for heading, paras in sections:
        pdf.set_font("helvetica", "B", 14)
        pdf.cell(0, 10, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "", 11)
        for para in paras:
            pdf.multi_cell(0, 6, para)
            pdf.ln(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


@pytest.fixture
def pdf_factory(tmp_path):
    def _make(name: str, title: str, sections: list[tuple[str, list[str]]]) -> Path:
        return make_pdf(tmp_path / name, title, sections)
    return _make


requires_services = pytest.mark.skipif(
    os.environ.get("COMPLIANCE_IT") != "1",
    reason="integration tests require COMPLIANCE_IT=1 and running ES/Ollama",
)
