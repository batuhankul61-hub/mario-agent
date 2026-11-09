"""Mario's long-term memory: notes, documents and e-mails in one vector store.

Every chunk has a citation id that names where it came from: `not-7` for a
note said by voice, `kira.md#2` for the third chunk of a document,
`mail-<gmail id>#0` for an e-mail."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from .chunking import chunk_text
from .embeddings import Embedder
from .loaders import find_documents, read_document
from .store import Hit, Record, VectorStore

log = logging.getLogger(__name__)

KINDS = ("note", "document", "email")


@dataclass
class IndexReport:
    added: list[str]
    unchanged: int
    removed: list[str]
    failed: list[str]
    chunks: int


def embed_input(record: Record) -> str:
    # The title travels with the chunk: "Belge: araba-bakim.md" helps a query
    # about the car find a chunk that only says "yağ değişimi".
    return f"{record.title}\n{record.text}"


class Memory:
    def __init__(
        self, store: VectorStore, embedder: Embedder, chunk_size: int = 800, overlap: int = 150
    ):
        self.store = store
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.overlap = overlap

    async def _add(self, records: list[Record]) -> None:
        vectors = await self.embedder.embed([embed_input(r) for r in records])
        self.store.add(records, vectors)

    # ---------- notes ----------

    async def remember(self, text: str) -> Record:
        text = text.strip()
        if not text:
            raise ValueError("empty note")
        note_id = f"not-{self.store.next_counter('note')}"
        record = Record(id=note_id, source=note_id, kind="note", title="Not", text=text)
        await self._add([record])
        return record

    def forget(self, note_id: str) -> bool:
        record = self.store.get(note_id)
        if record is None or record.kind != "note":
            return False
        return self.store.delete_source(note_id) > 0

    # ---------- documents ----------

    def _chunks(self, source: str, kind: str, title: str, text: str) -> list[Record]:
        return [
            Record(
                id=f"{source}#{c.index}",
                source=source,
                kind=kind,
                title=f"{title} / {c.heading}" if c.heading else title,
                text=c.text,
            )
            for c in chunk_text(text, self.chunk_size, self.overlap)
        ]

    async def index_folder(self, folder: Path) -> IndexReport:
        """Embeds new and changed files, drops chunks of deleted ones."""
        known = self.store.digests("document")
        report = IndexReport(added=[], unchanged=0, removed=[], failed=[], chunks=0)
        seen = set()
        for path in find_documents(folder):
            source = path.relative_to(folder).as_posix()
            seen.add(source)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if known.get(source) == digest:
                report.unchanged += 1
                continue
            try:
                text = await asyncio.to_thread(read_document, path)
            except Exception as exc:
                log.warning("Could not read %s: %s", path, exc)
                report.failed.append(source)
                continue
            records = self._chunks(source, "document", f"Belge: {source}", text)
            self.store.delete_source(source)
            if records:
                await self._add(records)
            self.store.set_digest(source, "document", digest)
            report.added.append(source)
            report.chunks += len(records)
        for source in sorted(set(known) - seen):
            self.store.delete_source(source)
            report.removed.append(source)
        return report

    # ---------- e-mail ----------

    async def index_emails(self, messages: list[dict]) -> int:
        """Messages from gmail.fetch_messages. Re-indexing a message replaces it."""
        total = 0
        for m in messages:
            source = f"mail-{m['id']}"
            title = f"E-posta: {m.get('from', '')} / {m.get('subject', '')} / {m.get('date', '')}"
            records = self._chunks(source, "email", title, m.get("body", ""))
            self.store.delete_source(source)
            if records:
                await self._add(records)
                self.store.set_digest(source, "email", m["id"])
                total += len(records)
        return total

    # ---------- search ----------

    async def search(
        self, query: str, k: int = 5, kinds: set[str] | None = None, min_score: float = 0.0
    ) -> list[Hit]:
        query = query.strip()
        if not query:
            return []
        vector = await self.embedder.embed([query], query=True)
        return self.store.search(vector[0], k=k, kinds=kinds, min_score=min_score)

    def stats(self) -> dict[str, int]:
        counts = self.store.count()
        return {kind: counts.get(kind, 0) for kind in KINDS}
