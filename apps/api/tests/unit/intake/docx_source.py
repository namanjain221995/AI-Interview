"""Independent re-extraction of ``Intake_Interview_Kit.docx`` (zipfile + ElementTree only).

The kit tests compare the YAML against this extraction, so any drift between the DOCX and
``samples/intake/intake_kit_v1.yaml`` fails the suite. ``INTAKE_KIT_DOCX`` overrides the location;
by default the DOCX is looked up at the repository root above this file.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_NAME = "Intake_Interview_Kit.docx"


def find_docx() -> Path | None:
    override = os.environ.get("INTAKE_KIT_DOCX")
    if override:
        path = Path(override)
        return path if path.is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / DOCX_NAME
        if candidate.is_file():
            return candidate
    return None


def _text(paragraph: ElementTree.Element) -> str:
    out: list[str] = []
    for node in paragraph.iter():
        if node.tag == W + "t":
            out.append(node.text or "")
        elif node.tag == W + "tab":
            out.append("\t")
        elif node.tag in (W + "br", W + "cr") and node.get(W + "type") != "page":
            out.append("\n")
    return "".join(out)


@dataclass(frozen=True)
class DocxKit:
    sha256: str
    paragraphs: tuple[str, ...]  # body paragraphs outside tables, in order, non-empty
    tables: tuple[tuple[tuple[tuple[str, ...], ...], ...], ...]  # table -> row -> cell -> paragraphs

    @property
    def all_text(self) -> str:
        """Every paragraph (body and table cells), one per line."""
        lines = list(self.paragraphs)
        for table in self.tables:
            for row in table:
                for cell in row:
                    lines.extend(cell)
        return "\n".join(lines)

    def table_starting(self, first_cell: str) -> tuple[tuple[tuple[str, ...], ...], ...]:
        """The table whose first cell's first paragraph equals ``first_cell``."""
        for table in self.tables:
            if table and table[0] and table[0][0] and table[0][0][0] == first_cell:
                return table
        raise LookupError(first_cell)

    def single_cell_tables(self, prefix: str) -> list[tuple[str, ...]]:
        """Paragraphs of the one-cell tables (passages, topic cards) whose header starts with ``prefix``."""
        found = []
        for table in self.tables:
            if len(table) == 1 and len(table[0]) == 1 and table[0][0] and table[0][0][0].startswith(prefix):
                found.append(table[0][0])
        return found


@lru_cache(maxsize=2)
def read_docx(path: Path) -> DocxKit:
    data = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))  # noqa: S314 - local test fixture
    body = root.find(W + "body")
    assert body is not None
    paragraphs: list[str] = []
    tables: list[tuple[tuple[tuple[str, ...], ...], ...]] = []
    for child in body:
        if child.tag == W + "p":
            text = _text(child)
            if text.strip():
                paragraphs.append(text)
        elif child.tag == W + "tbl":
            rows = []
            for row in child.findall(W + "tr"):
                cells = []
                for cell in row.findall(W + "tc"):
                    cells.append(
                        tuple(t for t in (_text(p) for p in cell.findall(".//" + W + "p")) if t.strip())
                    )
                rows.append(tuple(cells))
            tables.append(tuple(rows))
    return DocxKit(hashlib.sha256(data).hexdigest(), tuple(paragraphs), tuple(tables))
