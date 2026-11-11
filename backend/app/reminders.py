"""Reminders and timers: kept in data/reminders.json, fired by app/scheduler.py.

The model gives either minutes from now or a local clock time; parsing the time
here (not in the model) keeps "in 10 minutes" exact and the spoken summary
consistent with what will actually fire."""

import datetime as dt
import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_AHEAD = dt.timedelta(days=366)
KEEP_FIRED = 50


@dataclass
class Reminder:
    id: int
    text: str
    due: str  # local time, ISO, seconds precision
    kind: str = "reminder"  # or "timer"
    fired: bool = False

    @property
    def due_at(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.due)


class Reminders:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: list[Reminder] = self._load()

    def _load(self) -> list[Reminder]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [Reminder(**r) for r in raw]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self) -> None:
        # Fired ones are kept only for a while, so the file stays small.
        fired = [r for r in self._items if r.fired][-KEEP_FIRED:]
        self._items = [r for r in self._items if not r.fired] + fired
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([asdict(r) for r in self._items], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def add(self, text: str, due: dt.datetime, kind: str = "reminder") -> Reminder:
        with self._lock:
            next_id = max((r.id for r in self._items), default=0) + 1
            item = Reminder(next_id, text, due.isoformat(timespec="seconds"), kind)
            self._items.append(item)
            self._save()
            return item

    def pending(self) -> list[Reminder]:
        with self._lock:
            return sorted((r for r in self._items if not r.fired), key=lambda r: r.due)

    def cancel(self, reminder_id: int) -> Reminder | None:
        with self._lock:
            for r in self._items:
                if r.id == reminder_id and not r.fired:
                    self._items.remove(r)
                    self._save()
                    return r
            return None

    def pop_due(self, now: dt.datetime | None = None) -> list[Reminder]:
        """Marks every reminder whose time has come as fired and returns them."""
        now = now or dt.datetime.now()
        with self._lock:
            due = [r for r in self._items if not r.fired and r.due_at <= now]
            for r in due:
                r.fired = True
            if due:
                self._save()
            return due


_CLOCK = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def parse_due(
    in_minutes: object = None, at: object = None, now: dt.datetime | None = None
) -> dt.datetime | str:
    """The due time, or a Turkish error message to speak."""
    now = (now or dt.datetime.now()).replace(microsecond=0)
    if in_minutes not in (None, ""):
        try:
            minutes = float(in_minutes)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "Kaç dakika sonra olduğunu anlayamadım."
        if minutes <= 0:
            return "Süre sıfırdan büyük olmalı."
        due = now + dt.timedelta(minutes=minutes)
    elif isinstance(at, str) and at.strip():
        text = at.strip()
        if m := _CLOCK.match(text):
            hour, minute = int(m[1]), int(m[2])
            if hour > 23 or minute > 59:
                return f"'{text}' geçerli bir saat değil."
            due = now.replace(hour=hour, minute=minute, second=0)
            if due <= now:  # "at 9" said in the evening means tomorrow
                due += dt.timedelta(days=1)
        else:
            try:
                due = dt.datetime.fromisoformat(text)
            except ValueError:
                return f"'{text}' zamanını anlayamadım; 'YYYY-AA-GGTSS:DD' ya da 'SS:DD' olmalı."
            if due.tzinfo:
                due = due.astimezone().replace(tzinfo=None)
            if due <= now:
                return "Bu zaman geçmişte kaldı."
    else:
        return "Ne zaman hatırlatayım? Dakika ya da saat söyle."
    if due - now > MAX_AHEAD:
        return "En fazla bir yıl sonrası için hatırlatma kurabilirim."
    return due


def spoken(due: dt.datetime, now: dt.datetime | None = None) -> str:
    """'bugün 14:30', 'yarın 09:00' or '17 Mart 09:00'."""
    from .tools.web import MONTHS  # noqa: PLC0415

    now = now or dt.datetime.now()
    days = (due.date() - now.date()).days
    day = (
        "bugün"
        if days == 0
        else "yarın"
        if days == 1
        else f"{due.day} {MONTHS[due.month - 1]}" + (f" {due.year}" if due.year != now.year else "")
    )
    return f"{day} {due:%H:%M}"
