"""Local vector store: one SQLite file, vectors as float32 blobs, brute-force
cosine search with numpy.

A personal memory holds thousands of chunks, not millions, so an exact scan of
an in-memory matrix takes well under a millisecond and needs no index server.
The matrix is rebuilt lazily after a write."""

import datetime as dt
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id      TEXT PRIMARY KEY,   -- citation id: "kira.md#2", "not-7", "mail-<gmail id>#0"
    source  TEXT NOT NULL,      -- file path relative to the docs folder, note id or mail id
    kind    TEXT NOT NULL,      -- note | document | email
    title   TEXT NOT NULL,
    text    TEXT NOT NULL,
    created TEXT NOT NULL,
    vector  BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source);
CREATE TABLE IF NOT EXISTS sources (
    source  TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    digest  TEXT NOT NULL       -- sha256 of the file, so unchanged files are not re-embedded
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Record:
    id: str
    source: str
    kind: str
    title: str
    text: str
    created: str = ""


@dataclass(frozen=True)
class Hit:
    record: Record
    score: float


class VectorStore:
    def __init__(self, path: Path | str, dim: int):
        self.dim = dim
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._matrix: np.ndarray | None = None
        self._records: list[Record] = []

    # ---------- writes ----------

    def add(self, records: list[Record], vectors: np.ndarray) -> None:
        if len(records) != len(vectors):
            raise ValueError("one vector per record")
        if len(records) and vectors.shape[1] != self.dim:
            raise ValueError(f"expected {self.dim}-dim vectors, got {vectors.shape[1]}")
        now = dt.datetime.now().isoformat(timespec="seconds")
        rows = [
            (
                r.id,
                r.source,
                r.kind,
                r.title,
                r.text,
                r.created or now,
                v.astype(np.float32).tobytes(),
            )
            for r, v in zip(records, vectors)
        ]
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?)", rows)
            self._matrix = None

    def delete_source(self, source: str) -> int:
        with self._lock, self._db:
            n = self._db.execute("DELETE FROM chunks WHERE source = ?", (source,)).rowcount
            self._db.execute("DELETE FROM sources WHERE source = ?", (source,))
            self._matrix = None
        return n

    def set_digest(self, source: str, kind: str, digest: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO sources VALUES (?,?,?)", (source, kind, digest)
            )

    def digests(self, kind: str) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT source, digest FROM sources WHERE kind = ?", (kind,))
            return dict(rows.fetchall())

    def next_counter(self, name: str) -> int:
        with self._lock, self._db:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (name,)).fetchone()
            value = int(row[0]) + 1 if row else 1
            self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (name, str(value)))
        return value

    # ---------- reads ----------

    def get(self, chunk_id: str) -> Record | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, source, kind, title, text, created FROM chunks WHERE id = ?",
                (chunk_id,),
            ).fetchone()
        return Record(*row) if row else None

    def count(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT kind, COUNT(*) FROM chunks GROUP BY kind").fetchall()
        return dict(rows)

    def _load(self) -> tuple[np.ndarray, list[Record]]:
        with self._lock:
            if self._matrix is None:
                rows = self._db.execute(
                    "SELECT id, source, kind, title, text, created, vector FROM chunks ORDER BY rowid"
                ).fetchall()
                self._records = [Record(*row[:6]) for row in rows]
                self._matrix = (
                    np.vstack([np.frombuffer(row[6], dtype=np.float32) for row in rows])
                    if rows
                    else np.zeros((0, self.dim), dtype=np.float32)
                )
            return self._matrix, self._records

    def search(
        self, query: np.ndarray, k: int = 5, kinds: set[str] | None = None, min_score: float = 0.0
    ) -> list[Hit]:
        """Top-k records by cosine similarity (vectors are unit length, so a dot product)."""
        matrix, records = self._load()
        if not len(records) or k <= 0:
            return []
        scores = matrix @ np.asarray(query, dtype=np.float32).reshape(-1)
        if kinds:
            mask = np.array([r.kind in kinds for r in records])
            scores = np.where(mask, scores, -np.inf)
        k = min(k, len(records))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.lexsort((top, -scores[top]))]  # best first, older first on a tie
        return [
            Hit(records[i], float(scores[i]))
            for i in top
            if np.isfinite(scores[i]) and scores[i] >= min_score
        ]

    def close(self) -> None:
        self._db.close()
