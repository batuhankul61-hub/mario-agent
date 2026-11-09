"""Voice confirmation for destructive tools.

The model can't approve its own actions. A destructive tool call only parks
the action here and returns a confirmation id; the action runs when
`confirm_action` comes back *and* the user's own transcribed words since the
question contain a clear yes and no refusal. The browser sends those words
along with the call, so a model that "hears" a yes that was never said gets
`not_confirmed`.
"""

import re
import secrets
import time
from dataclasses import dataclass, field

YES = {
    "evet",
    "eminim",
    "onaylıyorum",
    "onayladım",
    "onayla",
    "onay",
    "tamam",
    "olur",
    "kesinlikle",
    "aynen",
    "yap",
    "yapabilirsin",
    "başlat",
    "yes",
    "yeah",
    "yep",
    "sure",
    "confirm",
    "confirmed",
}
NO = {
    "hayır",
    "yok",
    "iptal",
    "vazgeç",
    "vazgeçtim",
    "dur",
    "durdur",
    "bekle",
    "yapma",
    "etme",
    "istemiyorum",
    "değil",
    "değilim",
    "sakın",
    "no",
    "nope",
    "cancel",
    "stop",
    "don't",
    "dont",
}


def normalize(text: str) -> list[str]:
    # str.lower() turns "İ" into "i̇" (i + combining dot), which breaks matching.
    text = text.replace("İ", "i").replace("I", "ı").lower()
    return re.findall(r"[\w']+", text)


def is_affirmative(utterances: list[str]) -> bool:
    words = set(normalize(" ".join(utterances)))
    return bool(words & YES) and not words & NO


@dataclass
class Pending:
    id: str
    tool: str
    args: dict
    question: str
    created: float = field(default_factory=time.monotonic)


class PendingActions:
    def __init__(self, ttl_seconds: float):
        self.ttl = ttl_seconds
        self._items: dict[str, Pending] = {}

    def add(self, tool: str, args: dict, question: str) -> Pending:
        self._expire()
        # Short, because the model has to copy it back in confirm_action.
        pending = Pending(id=secrets.token_hex(3), tool=tool, args=args, question=question)
        self._items[pending.id] = pending
        return pending

    def get(self, pending_id: str) -> Pending | None:
        self._expire()
        return self._items.get(pending_id)

    def pop(self, pending_id: str) -> Pending | None:
        self._expire()
        return self._items.pop(pending_id, None)

    def clear(self) -> None:
        self._items.clear()

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, p in self._items.items() if now - p.created >= self.ttl]:
            del self._items[key]
