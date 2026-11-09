"""Reads the documents Mario remembers: PDF, Markdown and plain text."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
MAX_BYTES = 20 * 1024 * 1024


def read_pdf(path: Path) -> str:
    from pypdf import PdfReader  # noqa: PLC0415  # only needed when there is a PDF

    reader = PdfReader(path)
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return read_pdf(path)
    return path.read_text(encoding="utf-8", errors="replace")


def find_documents(folder: Path) -> list[Path]:
    """Supported files under `folder`, skipping hidden or `_` folders, the folder's own
    README and huge files."""
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part.startswith((".", "_")) for part in rel.parts) or rel == Path("README.md"):
            continue
        if path.is_file() and path.suffix.lower() in SUFFIXES:
            if path.stat().st_size > MAX_BYTES:
                log.warning("Skipping %s: larger than %d bytes", path, MAX_BYTES)
                continue
            out.append(path)
    return out
