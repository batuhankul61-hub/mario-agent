"""Regression run for Mario's memory: retrieval hit-rate and LLM-as-judge answer quality.

    cd backend
    uv run python evals/run_eval.py                    # full run, compares to evals/baseline.json
    uv run python evals/run_eval.py --retrieval-only   # embeddings only, no agent or judge
    uv run python evals/run_eval.py --save-baseline    # accept this run as the new baseline

Indexes evals/corpus/ and evals/notes.jsonl into a temporary store with the
real Gemini embeddings, asks every question in evals/questions.jsonl through
the memory researcher agent, and has a second model grade each answer against
the reference. Writes evals/results/latest.{json,md}. Exits with 1 when a metric
dropped more than --tolerance below the baseline.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import MemoryResearcher
from app.config import get_settings
from app.memory import GeminiEmbedder, Memory, VectorStore, gemini_client
from google.adk.models import Gemini
from google.genai import Client, types
from pydantic import BaseModel, Field

from evals.metrics import (
    DEFAULT_TOLERANCE,
    Case,
    markdown_table,
    regressions,
    source_of,
    summarize,
)

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
BASELINE = HERE / "baseline.json"

JUDGE_PROMPT = """Bir kişisel asistanın hafızadan verdiği cevabı değerlendiriyorsun.

Soru: {question}
Referans cevap: {reference}
Asistanın cevabı: {answer}
Asistanın dayandığı parçalar:
{context}

Puanla:
- correctness (1-5): cevap referansla ne kadar örtüşüyor? 5 = tamamen doğru ve eksiksiz, 4 = doğru ama küçük bir eksik,
  3 = kısmen doğru, 2 = çoğunlukla yanlış, 1 = yanlış ya da cevap yok.
  Referans "hafızada yok" diyorsa: asistan bilmediğini söylediyse 5, bir cevap uydurduysa 1.
- faithful: cevaptaki her bilgi yukarıdaki parçalarda geçiyor mu? Parça yoksa ve asistan bilmediğini söylediyse true.
- reason: tek kısa cümle."""


class Judgement(BaseModel):
    correctness: int = Field(ge=1, le=5)
    faithful: bool
    reason: str


def load_cases(path: Path) -> list[Case]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [Case(r["id"], r["question"], r["sources"], r["reference"]) for r in rows]


async def build_memory(client: Client, workdir: Path, chunk_size: int, overlap: int) -> Memory:
    s = get_settings()
    store = VectorStore(workdir / "eval.sqlite", s.embedding_dim)
    embedder = GeminiEmbedder(client, s.embedding_model, s.embedding_dim)
    memory = Memory(store, embedder, chunk_size, overlap)
    report = await memory.index_folder(HERE / "corpus")
    for line in (HERE / "notes.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            await memory.remember(json.loads(line)["text"])
    print(
        f"Indexed {len(report.added)} documents ({report.chunks} chunks) + notes: {memory.stats()}"
    )
    return memory


async def judge(client: Client, model: str, case: Case, memory: Memory, cited: list[str]) -> None:
    context = "\n".join(
        f"[{cid}] {rec.text}" for cid in cited if (rec := memory.store.get(cid)) is not None
    )
    prompt = JUDGE_PROMPT.format(
        question=case.question,
        reference=case.reference,
        answer=case.answer if case.found else "(Hafızamda bununla ilgili bilgi bulamadım.)",
        context=context or "(yok)",
    )
    res = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=Judgement,
        ),
    )
    verdict = (
        res.parsed
        if isinstance(res.parsed, Judgement)
        else Judgement.model_validate_json(res.text or "")
    )
    case.correctness, case.faithful, case.judge_reason = (
        verdict.correctness,
        verdict.faithful,
        verdict.reason,
    )


class Pacer:
    """Spaces calls to stay under a requests-per-minute quota (the free tier
    allows 5-15 requests per minute per model, Nov 2026)."""

    def __init__(self, rpm: float):
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


async def run_case(
    case: Case,
    memory: Memory,
    researcher: MemoryResearcher | None,
    client: Client,
    args: argparse.Namespace,
    gate: asyncio.Semaphore,
    pacer: Pacer,
) -> None:
    async with gate:
        try:
            hits = await memory.search(case.question, k=args.k)
            case.retrieved_sources = [source_of(h.record.id) for h in hits]
            if researcher is None:
                return
            await pacer.wait()
            res = await researcher.ask(case.question)
            case.found, case.answer, case.dropped = res.found, res.answer, len(res.dropped)
            case.cited_sources = [source_of(cid) for cid in res.cited]
            await judge(client, args.judge_model, case, memory, res.cited)
        except Exception as exc:  # one failed question must not lose the whole run
            case.error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            print(f"! {case.id} {case.error}")
            return
        mark = "✓" if (case.correctness or 0) >= 4 else "✗"
        print(
            f"{mark} {case.id} [{case.correctness}] {case.question} → {case.answer or '(bulunamadı)'}"
        )


async def main() -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--k", type=int, default=s.memory_top_k)
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions")
    parser.add_argument("--agent-model", default=s.agent_model)
    parser.add_argument("--judge-model", default=s.judge_model)
    parser.add_argument("--chunk-size", type=int, default=s.chunk_size)
    parser.add_argument("--chunk-overlap", type=int, default=s.chunk_overlap)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--rpm", type=float, default=4, help="questions per minute (free-tier quota; 0 = no limit)"
    )
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--save-baseline", action="store_true")
    args = parser.parse_args()

    if not s.gemini_api_key:
        print("GEMINI_API_KEY is not set (backend/.env); the eval needs the real models.")
        return 2

    cases = load_cases(HERE / "questions.jsonl")
    if args.limit:
        cases = cases[: args.limit]
    client = gemini_client(s)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        memory = await build_memory(client, Path(tmp), args.chunk_size, args.chunk_overlap)
        try:
            researcher = (
                None
                if args.retrieval_only
                else MemoryResearcher(memory, Gemini(model=args.agent_model, client=client), args.k)
            )
            gate, pacer = asyncio.Semaphore(args.concurrency), Pacer(args.rpm)
            await asyncio.gather(
                *(run_case(c, memory, researcher, client, args, gate, pacer) for c in cases)
            )
        finally:
            memory.store.close()

    summary = summarize(cases, args.k)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    run = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - started, 1),
        "config": {
            "embedding_model": s.embedding_model,
            "embedding_dim": s.embedding_dim,
            "chunk_size": args.chunk_size,
            "chunk_overlap": args.chunk_overlap,
            "k": args.k,
            "agent_model": None if args.retrieval_only else args.agent_model,
            "judge_model": None if args.retrieval_only else args.judge_model,
        },
        "summary": summary,
        "cases": [c.__dict__ for c in cases],
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    table = markdown_table(summary, baseline["summary"] if baseline else None)
    (RESULTS / "latest.md").write_text(
        f"# Memory eval, {run['time']}\n\n```\n{json.dumps(run['config'], indent=2)}\n```\n\n{table}\n",
        encoding="utf-8",
    )
    print()
    print(table)

    if args.save_baseline:
        BASELINE.write_text(
            json.dumps(
                {k: run[k] for k in ("time", "config", "summary")}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"\nBaseline saved to {BASELINE.name}.")
        return 0
    if baseline:
        # Only metrics present in both runs are compared (a retrieval-only run has no answer metrics).
        dropped = regressions(summary, baseline["summary"], args.tolerance)
        if dropped:
            print("\nREGRESSION:")
            for key, old, new in dropped:
                print(f"  {key}: {old} → {new}")
            return 1
        print("\nNo regression against the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
