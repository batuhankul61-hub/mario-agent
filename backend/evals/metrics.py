"""Scoring for the memory eval. Pure functions, so pytest covers them offline."""

from dataclasses import dataclass, field

# A summary metric may drop by at most this much before the run counts as a regression.
DEFAULT_TOLERANCE = 0.05


@dataclass
class Case:
    id: str
    question: str
    sources: list[str]  # expected sources; empty = the memory has no answer
    reference: str
    # Filled in by the run:
    retrieved_sources: list[str] = field(default_factory=list)  # ranked, one per chunk
    found: bool | None = None
    answer: str = ""
    cited_sources: list[str] = field(default_factory=list)
    dropped: int = 0
    correctness: int | None = None  # judge, 1-5
    faithful: bool | None = None  # judge: answer supported by the cited chunks
    judge_reason: str = ""
    error: str = ""  # the question failed (quota, network); left out of the scores

    @property
    def answerable(self) -> bool:
        return bool(self.sources)


def source_of(chunk_id: str) -> str:
    """ "kira.md#2" → "kira.md"; note ids are their own source."""
    return chunk_id.split("#", 1)[0]


def first_hit_rank(retrieved: list[str], expected: list[str]) -> int | None:
    """1-based rank of the first retrieved chunk from an expected source."""
    for rank, source in enumerate(retrieved, 1):
        if source in expected:
            return rank
    return None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def summarize(cases: list[Case], k: int) -> dict[str, float | int | None]:
    scored = [c for c in cases if not c.error]
    answerable = [c for c in scored if c.answerable]
    unanswerable = [c for c in scored if not c.answerable]
    ranks = [first_hit_rank(c.retrieved_sources, c.sources) for c in answerable]

    def hit_at(n: int) -> float | None:
        return _mean([1.0 if r is not None and r <= n else 0.0 for r in ranks])

    summary: dict[str, float | int | None] = {
        "questions": len(cases),
        "errors": sum(1 for c in cases if c.error),
        "hit@1": hit_at(1),
        "hit@3": hit_at(3),
        f"hit@{k}": hit_at(k),
        "mrr": _mean([1.0 / r if r else 0.0 for r in ranks]),
    }
    answered = [c for c in scored if c.found is not None]
    if not answered:
        return summary
    judged = [c for c in answerable if c.correctness is not None]
    found = [c for c in answerable if c.found]
    cited = [s for c in found for s in c.cited_sources]
    expected = {id(c): set(c.sources) for c in found}
    good_cites = sum(1 for c in found for s in c.cited_sources if s in expected[id(c)])
    summary.update(
        {
            # Judge score >= 4 counts as a correct answer; a "not found" scores 1.
            "answer_accuracy": _mean([1.0 if (c.correctness or 0) >= 4 else 0.0 for c in judged]),
            "mean_correctness": _mean([float(c.correctness or 0) for c in judged]),
            "faithfulness": _mean(
                [1.0 if c.faithful else 0.0 for c in found if c.faithful is not None]
            ),
            "citation_precision": round(good_cites / len(cited), 3) if cited else None,
            "abstention_accuracy": _mean([0.0 if c.found else 1.0 for c in unanswerable]),
            "dropped_citations": sum(c.dropped for c in answered),
        }
    )
    return summary


# ---------- tool routing (evals/run_tool_eval.py) ----------


def _norm(value) -> str:
    text = str(value).replace("İ", "i").replace("I", "ı").lower()
    return " ".join(text.split())


def tool_call_matches(
    expected_tool: str | list[str] | None, expected_args: dict, call: dict | None
) -> bool:
    """The first function call the model made against what the case expects.

    expected_tool None: the model must answer without a tool; a list: any of
    these is a right first step (e.g. listing before cancelling). Expected args are
    a subset check; a string value matches when it appears in the actual value
    (so "ahmet" matches "Ahmet Yılmaz"), other values must be equal."""
    name = call.get("name") if call else None
    if expected_tool is None:
        return name is None
    allowed = [expected_tool] if isinstance(expected_tool, str) else expected_tool
    if name not in allowed:
        return False
    if name != allowed[0]:
        return True  # an alternative first step; args were written for the main one
    actual = call.get("args") or {}
    for key, want in expected_args.items():
        if key not in actual:
            return False
        got = actual[key]
        if isinstance(want, str):
            if _norm(want) not in _norm(got):
                return False
        elif got != want:
            return False
    return True


def summarize_tools(results: list[dict]) -> dict[str, float | int | None]:
    """results: {"id", "group", "ok", "error"}; accuracy overall and per group."""
    scored = [r for r in results if not r.get("error")]
    summary: dict[str, float | int | None] = {
        "cases": len(results),
        "errors": len(results) - len(scored),
        "tool_accuracy": _mean([1.0 if r["ok"] else 0.0 for r in scored]),
    }
    for group in sorted({r["group"] for r in scored}):
        summary[f"tool_accuracy_{group}"] = _mean(
            [1.0 if r["ok"] else 0.0 for r in scored if r["group"] == group]
        )
    return summary


# Counts, not rates; never compared against the baseline.
NOT_RATES = {"questions", "errors", "dropped_citations", "cases"}


def regressions(
    current: dict, baseline: dict, tolerance: float = DEFAULT_TOLERANCE
) -> list[tuple[str, float, float]]:
    """Metrics that dropped by more than `tolerance` since the baseline."""
    out = []
    for key, old in baseline.items():
        new = current.get(key)
        if key in NOT_RATES or not isinstance(old, int | float) or not isinstance(new, int | float):
            continue
        if new < old - tolerance:
            out.append((key, old, new))
    return out


def markdown_table(summary: dict, baseline: dict | None = None) -> str:
    rows = ["| Metrik | Değer | Baseline |", "|---|---|---|"]
    for key, value in summary.items():
        old = baseline.get(key) if baseline else None
        rows.append(
            f"| {key} | {value if value is not None else '-'} | {old if old is not None else '-'} |"
        )
    return "\n".join(rows)
