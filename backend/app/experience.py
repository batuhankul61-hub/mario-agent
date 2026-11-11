"""Lessons Mario keeps from finished Claude jobs, the idea behind the experience
memory of simular-ai/Agent-S: after a screen task Claude writes one sentence on
what worked or what to watch out for, and the next similar task (or skill being
learned) starts with those lessons in its prompt.

Stored as JSON lines in data/experience.jsonl. Similar lessons are found with
the memory's Gemini embeddings (vectors cached in experience.vectors.npz);
plain word overlap is the fallback without a key or network."""

import datetime as dt
import hashlib
import json
import logging
import re
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

MAX_ENTRIES = 500
# Cosine similarity below this is "not about the same thing" for
# gemini-embedding-001 at 768 dimensions on short Turkish sentences.
MIN_SCORE = 0.6
STOPWORDS = {
    "bir",
    "ile",
    "icin",
    "sonra",
    "once",
    "gibi",
    "olan",
    "bunu",
    "sunu",
    "ona",
    "ben",
    "beni",
    "bana",
    "lutfen",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
}


def words(text: str) -> set[str]:
    text = text.replace("İ", "i").replace("I", "i").lower()
    for a, b in zip("ışğüöç", "isguoc"):
        text = text.replace(a, b)
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3 and w not in STOPWORDS}


def load(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-MAX_ENTRIES:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def record(path: Path, *, kind: str, task: str, ok: bool, result: str, lesson: str) -> None:
    lesson = lesson.strip()
    if not lesson or lesson.lower().rstrip(".") == "yok":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "task": task,
        "ok": ok,
        "result": result[:300],
        "lesson": lesson[:300],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def relevant(path: Path, task: str, limit: int = 5) -> list[dict]:
    """Past lessons whose task shares words with this one, best match first, newer first on a tie."""
    wanted = words(task)
    scored = []
    for i, entry in enumerate(load(path)):
        score = len(wanted & words(entry.get("task", "") + " " + entry.get("lesson", "")))
        if score:
            scored.append((score, i, entry))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [entry for _, _, entry in scored[:limit]]


def _entry_text(entry: dict) -> str:
    return f"{entry.get('task', '')} — {entry.get('lesson', '')}"


async def _vectors(path: Path, texts: list[str], embedder) -> np.ndarray:
    """One vector per text, cached next to the log so each lesson is embedded once."""
    cache_file = path.with_suffix(".vectors.npz")
    keys = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in texts]
    cached: dict[str, np.ndarray] = {}
    try:
        with np.load(cache_file) as data:
            if data["vectors"].shape[1:] == (embedder.dim,):
                cached = dict(zip(data["keys"].tolist(), data["vectors"]))
    except (OSError, ValueError, KeyError):
        pass
    missing = [i for i, k in enumerate(keys) if k not in cached]
    if missing:
        fresh = await embedder.embed([texts[i] for i in missing])
        for i, vec in zip(missing, fresh):
            cached[keys[i]] = vec
        keep = list(dict.fromkeys(keys))
        try:
            np.savez(cache_file, keys=np.array(keep), vectors=np.stack([cached[k] for k in keep]))
        except OSError:
            pass
    return np.stack([cached[k] for k in keys])


async def relevant_semantic(
    path: Path, task: str, embedder, limit: int = 5, min_score: float = MIN_SCORE
) -> list[dict]:
    """Past lessons ranked by embedding similarity: "WhatsApp'a yaz" finds a
    lesson about "mesaj gönderme" even without a shared word."""
    entries = load(path)
    if not entries:
        return []
    vecs = await _vectors(path, [_entry_text(e) for e in entries], embedder)
    query = (await embedder.embed([task], query=True))[0]
    scores = vecs @ query
    order = sorted(range(len(entries)), key=lambda i: (scores[i], i), reverse=True)
    return [entries[i] for i in order[:limit] if scores[i] >= min_score]


async def prompt_section_for(path: Path, task: str, embedder=None) -> str:
    """Semantic search when an embedder is available, word overlap otherwise
    (no Gemini key, network down) or when nothing is similar enough."""
    lessons: list[dict] = []
    if embedder is not None:
        try:
            lessons = await relevant_semantic(path, task, embedder)
        except Exception:
            log.warning("Semantic lesson search failed; using word overlap", exc_info=True)
    return _section(lessons or relevant(path, task))


def prompt_section(path: Path, task: str) -> str:
    return _section(relevant(path, task))


def _section(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = [
        f"- {'Başarılı' if e.get('ok') else 'Başarısız'} görev «{e.get('task', '')}»: {e.get('lesson', '')}"
        for e in lessons
    ]
    return (
        "\nBenzer önceki görevlerden çıkan dersler (bunlar senin notların; uygunsa kullan):\n"
        + "\n".join(lines)
        + "\n"
    )
