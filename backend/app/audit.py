"""Append-only record of every tool call, so "did it really run?" has an answer
that doesn't depend on what the model says."""

import datetime as dt
import json
import threading
from pathlib import Path

_lock = threading.Lock()


def record(path: Path, **entry) -> None:
    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"), **entry}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def tail(path: Path, n: int = 50) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(line) for line in lines if line.strip()]
