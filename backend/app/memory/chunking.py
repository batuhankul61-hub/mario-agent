"""Splits text into overlapping chunks for embedding.

Paragraphs are kept whole when they fit; longer ones are split at sentence
ends, and a sentence longer than a chunk is cut at a space. Each chunk after
the first starts with the last sentences of the previous one (up to `overlap`
characters), so a fact that straddles a boundary is still found whole."""

import re
from dataclasses import dataclass

SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    # The Markdown headings the chunk falls under, joined with " · "; "" if
    # none. Embedded with the chunk so "## Ödeme" sections stay findable by topic.
    heading: str = ""


def _units(paragraph: str, size: int) -> list[str]:
    if len(paragraph) <= size:
        return [paragraph]
    out: list[str] = []
    for sentence in SENTENCE_END.split(paragraph):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            out.append(sentence)
    return out


def _paragraphs(text: str) -> list[tuple[str, str]]:
    """(heading, paragraph) pairs; headings themselves are paragraphs too."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    heading = ""
    out = []
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"[ \t]+", " ", block).strip()
        if not block:
            continue
        first = block.splitlines()[0]
        if first.startswith("#"):
            heading = first.lstrip("#").strip()
        out.append((heading, " ".join(line.strip() for line in block.splitlines())))
    return out


def chunk_text(text: str, size: int = 800, overlap: int = 150) -> list[Chunk]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    units: list[tuple[str, str]] = [
        (heading, unit) for heading, para in _paragraphs(text) for unit in _units(para, size)
    ]
    chunks: list[Chunk] = []
    current: list[tuple[str, str]] = []

    def length(items: list[tuple[str, str]]) -> int:
        return sum(len(u) for _, u in items) + max(0, len(items) - 1)

    def emit() -> None:
        headings = " · ".join(dict.fromkeys(h for h, _ in current if h))
        chunks.append(Chunk(len(chunks), "\n".join(u for _, u in current), headings))

    for item in units:
        if current and length([*current, item]) > size:
            emit()
            tail: list[tuple[str, str]] = []
            for prev in reversed(current):
                if length([prev, *tail]) > overlap:
                    break
                tail.insert(0, prev)
            current = tail
            # The overlap must leave room for the new unit.
            while current and length([*current, item]) > size:
                current.pop(0)
        current.append(item)
    if current:
        emit()
    return chunks
