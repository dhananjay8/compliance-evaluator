"""Structure-aware .docx extraction.

A docx has no fixed pages — pagination is renderer-dependent — so instead of
physical pages this extractor emits *logical sections*: one ExtractedPage per
heading-delimited section, with ``metadata["heading_path"]`` carrying the full
hierarchy (e.g. ["Corporate Policy", "3. Policy Description"]). The chunker
never merges across page boundaries, so sections stay intact end-to-end.

Three document flavors are handled:

- Styled docs: ``w:pStyle`` values like ``Heading1``/``Heading2`` map to
  heading levels directly; ``TOC*`` styles (table-of-contents entries) are
  skipped.
- altChunk docs (e.g. Engaiz exports): ``document.xml`` is a shell; content
  lives in embedded ``word/chunk*.html`` parts, parsed via html.parser with
  ``h1``–``h6`` as headings.
- Flat docs (e.g. Drata exports): no styles — headings are inferred from
  paragraphs whose text runs are entirely bold and short.

Tables (``w:tbl`` / ``<table>``) are serialized as ``" | "``-joined rows kept
as a single block so they stay atomic through chunking.
"""

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from app.core.errors import DocumentExtractionError
from app.core.logging import get_logger
from app.schemas.document import DocumentRef, ExtractedDocument, ExtractedPage

log = get_logger(__name__)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_HEADING_STYLE = re.compile(r"^heading\s*([1-6])$", re.I)
_TOC_STYLE = re.compile(r"^toc|^table\s*of\s*contents", re.I)
_MAX_HEADING_LEN = 120
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text.replace("\xa0", " ")).strip()


# ---------------------------------------------------------------------------
# Native document.xml blocks
# ---------------------------------------------------------------------------

def _para_text(p: ET.Element) -> str:
    parts: list[str] = []
    for node in p.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append(" ")
        elif node.tag == f"{_W}br":
            parts.append("\n")
    return _clean("".join(parts))


def _para_style(p: ET.Element) -> str:
    ppr = p.find(f"{_W}pPr")
    if ppr is None:
        return ""
    style = ppr.find(f"{_W}pStyle")
    return (style.get(f"{_W}val") or "") if style is not None else ""


def _is_list_item(p: ET.Element) -> bool:
    ppr = p.find(f"{_W}pPr")
    return ppr is not None and ppr.find(f"{_W}numPr") is not None


def _is_all_bold(p: ET.Element) -> bool:
    """True when every text-bearing run is bold (flat-doc heading heuristic)."""
    saw_text = False
    for run in p.iter(f"{_W}r"):
        if not "".join(t.text or "" for t in run.iter(f"{_W}t")).strip():
            continue
        saw_text = True
        rpr = run.find(f"{_W}rPr")
        if rpr is None or (
            rpr.find(f"{_W}b") is None and rpr.find(f"{_W}bCs") is None
        ):
            return False
    return saw_text


def _table_text(tbl: ET.Element) -> str:
    rows: list[str] = []
    for tr in tbl.findall(f"{_W}tr"):
        cells = [_clean(" ".join(_para_text(p) for p in tc.iter(f"{_W}p")))
                 for tc in tr.findall(f"{_W}tc")]
        line = " | ".join(c for c in cells if c)
        if line:
            rows.append(line)
    return "\n".join(rows)


class _Block:
    __slots__ = ("kind", "level", "text")

    def __init__(self, kind: str, text: str, level: int = 0):
        self.kind = kind  # "heading" | "para" | "table"
        self.level = level
        self.text = text


def _iter_native_blocks(body: ET.Element) -> list[_Block]:
    blocks: list[_Block] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == f"{_W}p":
                text = _para_text(child)
                if not text:
                    continue
                style = _para_style(child)
                if _TOC_STYLE.match(style):
                    continue
                m = _HEADING_STYLE.match(style)
                if m:
                    blocks.append(_Block("heading", text, int(m.group(1))))
                elif _is_all_bold(child) and len(text) <= _MAX_HEADING_LEN \
                        and re.search(r"[A-Za-z0-9]", text) \
                        and not text.endswith((".", ";", ",")):
                    blocks.append(_Block("heading", text, 1))
                else:
                    if _is_list_item(child):
                        text = f"• {text}"
                    blocks.append(_Block("para", text))
            elif child.tag == f"{_W}tbl":
                text = _table_text(child)
                if text:
                    blocks.append(_Block("table", text))
            elif child.tag == f"{_W}sdt":
                content = child.find(f"{_W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    return blocks


# ---------------------------------------------------------------------------
# altChunk (embedded HTML) blocks
# ---------------------------------------------------------------------------

class _HtmlBlocks(HTMLParser):
    """Minimal block extractor: h1-h6 → heading, p/li → para, table → table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._buf: list[str] | None = None
        self._kind = "para"
        self._level = 0
        self._rows: list[str] | None = None
        self._cells: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._open("heading", int(tag[1]))
        elif tag == "p":
            self._open("para")
        elif tag == "li":
            self._open("para")
            self._buf.append("• ")
        elif tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []
        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")
            elif self._buf is not None:
                self._buf.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif self._buf is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li"):
            self._close()
        elif tag in ("td", "th") and self._cell is not None:
            self._cells.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            line = " | ".join(c for c in self._cells if c)
            if line:
                self._rows.append(line)
            self._cells = None
        elif tag == "table" and self._rows is not None:
            if self._rows:
                self.blocks.append(_Block("table", "\n".join(self._rows)))
            self._rows = None

    def _open(self, kind: str, level: int = 0) -> None:
        self._close()
        if self._rows is not None:
            return  # inside a table — cell markup only
        self._kind, self._level, self._buf = kind, level, []

    def _close(self) -> None:
        if self._buf is None:
            return
        text = _clean("".join(self._buf))
        if text:
            self.blocks.append(_Block(self._kind, text, self._level))
        self._buf = None


def _html_blocks(html: str) -> list[_Block]:
    parser = _HtmlBlocks()
    parser.feed(html)
    parser.close()
    parser._close()
    return parser.blocks


# ---------------------------------------------------------------------------
# Section assembly
# ---------------------------------------------------------------------------

def _sections(blocks: list[_Block]) -> list[tuple[list[str], str]]:
    """Group blocks into (heading_path, text) sections."""
    sections: list[tuple[list[str], list[str]]] = []
    stack: list[tuple[int, str]] = []  # (level, heading) — handles skipped levels
    current: list[str] = []
    for blk in blocks:
        if blk.kind == "heading":
            if current:
                sections.append(([t for _, t in stack], current))
            while stack and stack[-1][0] >= blk.level:
                stack.pop()
            stack.append((blk.level, blk.text))
            current = [blk.text]
        else:
            current.append(blk.text)
    if current:
        sections.append(([t for _, t in stack], current))
    return [(p, "\n\n".join(parts).strip()) for p, parts in sections]


class DocxExtractor:
    """Extracts logical sections (heading → content) from a .docx on disk."""

    def extract(self, doc_ref: DocumentRef, path: Path) -> ExtractedDocument:
        warnings: list[str] = []
        try:
            zf = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise DocumentExtractionError(
                f"Cannot open DOCX {doc_ref.filename}: {exc}",
                detail={"document_id": doc_ref.document_id},
            ) from exc

        with zf:
            try:
                document_xml = zf.read("word/document.xml")
            except KeyError as exc:
                raise DocumentExtractionError(
                    f"DOCX {doc_ref.filename} has no word/document.xml",
                    detail={"document_id": doc_ref.document_id},
                ) from exc

            root = ET.fromstring(document_xml)
            body = root.find(f"{_W}body")
            blocks: list[_Block] = []

            # altChunk: content lives in embedded HTML parts (rels → chunkN.html)
            alt_ids = [
                el.get(f"{_R}id")
                for el in (body.iter(f"{_W}altChunk") if body is not None else [])
            ]
            if alt_ids:
                rels = self._rels(zf)
                for rid in alt_ids:
                    target = rels.get(rid or "")
                    if not target:
                        warnings.append(f"altChunk {rid}: no relationship target")
                        continue
                    try:
                        html = zf.read(f"word/{target}").decode("utf-8", "replace")
                    except KeyError:
                        warnings.append(f"altChunk {rid}: missing part {target}")
                        continue
                    blocks.extend(_html_blocks(html))
                # any native paragraphs alongside the chunks (rare)
                if body is not None:
                    blocks.extend(_iter_native_blocks(body))
            elif body is not None:
                blocks = _iter_native_blocks(body)

        sections = [(p, t) for p, t in _sections(blocks) if t]
        if not sections:
            raise DocumentExtractionError(
                f"DOCX {doc_ref.filename} contains no extractable text",
                detail={"document_id": doc_ref.document_id},
            )

        pages = [
            ExtractedPage(
                page_number=i + 1,  # pseudo-page: logical section index
                text=text,
                headings=[hpath[-1]] if hpath else [],
                char_offset_start=0,
                char_offset_end=len(text),
                empty=False,
                metadata={"heading_path": hpath, "source_format": "docx"},
            )
            for i, (hpath, text) in enumerate(sections)
        ]
        return ExtractedDocument(
            document_id=doc_ref.document_id,
            assessment_id=doc_ref.assessment_id,
            filename=doc_ref.filename,
            sha256=doc_ref.sha256,
            page_count=len(pages),
            pages=pages,
            extraction_warnings=warnings,
        )

    @staticmethod
    def _rels(zf: zipfile.ZipFile) -> dict[str, str]:
        rels: dict[str, str] = {}
        try:
            root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
        except KeyError:
            return rels
        for rel in root.iter(f"{_REL}Relationship"):
            rid, target = rel.get("Id"), rel.get("Target")
            if rid and target:
                rels[rid] = target
        return rels
