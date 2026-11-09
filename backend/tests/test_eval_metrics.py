"""Scoring of the memory eval (evals/metrics.py)."""

import json
from pathlib import Path

from evals.metrics import Case, first_hit_rank, markdown_table, regressions, source_of, summarize

EVALS = Path(__file__).resolve().parent.parent / "evals"


def test_source_of_chunk_ids():
    assert source_of("alt/kira.md#3") == "alt/kira.md"
    assert source_of("not-4") == "not-4"


def test_first_hit_rank():
    assert first_hit_rank(["a.md", "b.md", "c.md"], ["c.md", "b.md"]) == 2
    assert first_hit_rank(["a.md"], ["z.md"]) is None


def case(i, sources, retrieved, **kw):
    return Case(i, "?", sources, "ref", retrieved_sources=retrieved, **kw)


def test_retrieval_only_summary():
    cases = [
        case("1", ["a.md"], ["a.md", "b.md"]),
        case("2", ["b.md"], ["a.md", "c.md", "b.md"]),
        case("3", ["c.md"], ["a.md"]),
        case("4", [], ["a.md"]),  # unanswerable: not part of the retrieval metrics
    ]
    s = summarize(cases, k=5)
    assert s["questions"] == 4
    assert (s["hit@1"], s["hit@3"], s["hit@5"]) == (0.333, 0.667, 0.667)
    assert s["mrr"] == round((1 + 1 / 3 + 0) / 3, 3)
    assert "answer_accuracy" not in s


def test_answer_summary():
    cases = [
        case(
            "1",
            ["a.md"],
            ["a.md"],
            found=True,
            cited_sources=["a.md"],
            correctness=5,
            faithful=True,
        ),
        case(
            "2",
            ["b.md"],
            ["b.md"],
            found=True,
            cited_sources=["b.md", "a.md"],
            correctness=3,
            faithful=False,
            dropped=1,
        ),
        case("3", ["c.md"], ["a.md"], found=False, correctness=1),
        case("4", [], ["a.md"], found=False, correctness=5),
        case("5", [], ["a.md"], found=True, cited_sources=["a.md"], correctness=1, faithful=True),
    ]
    s = summarize(cases, k=3)
    assert s["answer_accuracy"] == 0.333
    assert s["mean_correctness"] == 3.0
    assert s["faithfulness"] == 0.5
    assert s["citation_precision"] == 0.667
    assert s["abstention_accuracy"] == 0.5
    assert s["dropped_citations"] == 1


def test_failed_questions_are_left_out_of_the_scores():
    cases = [case("1", ["a.md"], ["a.md"]), case("2", ["b.md"], [], error="ClientError: 429")]
    s = summarize(cases, k=3)
    assert (s["questions"], s["errors"], s["hit@1"]) == (2, 1, 1.0)


def test_regressions_respect_tolerance_and_skip_counts():
    base = {
        "questions": 40,
        "hit@1": 0.9,
        "mrr": 0.95,
        "answer_accuracy": 0.8,
        "dropped_citations": 0,
    }
    now = {"questions": 10, "hit@1": 0.86, "mrr": 0.80, "dropped_citations": 3}
    assert regressions(now, base, tolerance=0.05) == [("mrr", 0.95, 0.80)]
    assert "| hit@1 | 0.86 | 0.9 |" in markdown_table(now, base)


def test_eval_set_is_well_formed():
    rows = [
        json.loads(line)
        for line in (EVALS / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) >= 30
    assert len({r["id"] for r in rows}) == len(rows)
    notes = (EVALS / "notes.jsonl").read_text(encoding="utf-8").splitlines()
    known = {p.name for p in (EVALS / "corpus").iterdir()} | {
        f"not-{i}" for i in range(1, len(notes) + 1)
    }
    for r in rows:
        assert set(r["sources"]) <= known, r["id"]
    assert sum(1 for r in rows if not r["sources"]) >= 3  # questions the memory can't answer
