"""Lessons Mario keeps from finished Claude jobs, the idea behind the experience
memory of simular-ai/Agent-S: after a screen task Claude writes one sentence on
what worked or what to watch out for, and the next similar task (or skill being
learned) starts with those lessons in its prompt.

Stored as JSON lines in data/experience.jsonl; similarity is plain word
overlap, which is enough for a few hundred short entries."""

import datetime as dt
import json
import re
from pathlib import Path

MAX_ENTRIES = 500
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


def prompt_section(path: Path, task: str) -> str:
    lessons = relevant(path, task)
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
