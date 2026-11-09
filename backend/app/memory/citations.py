"""Grounding check for answers drawn from memory.

The researcher agent cites chunks as [id]. A model can cite a source it never
retrieved, or invent one; such citations are removed, and an answer left with
no valid citation is not passed on as a fact. Same idea as Lucia's transcript
check: trust what the server saw, not what the model says it saw."""

import re
from dataclasses import dataclass, field

CITATION = re.compile(r"\[([^\[\]\n]{1,200})\]")


@dataclass
class Checked:
    text: str  # the answer with citation markers removed, ready to be spoken
    cited: list[str] = field(default_factory=list)  # valid ids, in order of first use
    dropped: list[str] = field(default_factory=list)  # ids the model cited but never retrieved

    @property
    def grounded(self) -> bool:
        return bool(self.cited)


def _ids(marker: str) -> list[str]:
    # "[a.md#0, not-3]" and "[a.md#0; not-3]" cite two chunks.
    return [part.strip() for part in re.split(r"[,;]", marker) if part.strip()]


def check_citations(answer: str, allowed: set[str]) -> Checked:
    cited: list[str] = []
    dropped: list[str] = []

    def strip(match: re.Match) -> str:
        for cid in _ids(match.group(1)):
            bucket = cited if cid in allowed else dropped
            if cid not in bucket:
                bucket.append(cid)
        return ""

    text = CITATION.sub(strip, answer)
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()
    return Checked(text=text, cited=cited, dropped=dropped)
