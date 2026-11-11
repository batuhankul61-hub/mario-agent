"""Regression run for Mario's tool routing: does a request reach the right tool?

    cd backend
    uv run python evals/run_tool_eval.py                  # compares to evals/tool_baseline.json
    uv run python evals/run_tool_eval.py --save-baseline  # accept this run as the new baseline

Every case in evals/tool_cases.jsonl (optionally after a few earlier turns) is
sent with Mario's own system instruction and the exact function declarations
locked into the Live token, and the first function call is graded with
evals.metrics.tool_call_matches. Writes evals/results/tools-latest.{json,md} and
exits with 1 when an accuracy dropped more than --tolerance below the baseline.

The Live model only talks audio, so a text model stands in for it (default:
the agent model). The number tracks prompt and tool-description changes; it
is not the Live model's exact accuracy. Learned skills are left out of the
prompt so the run does not depend on what this PC has learned.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.gemini import SYSTEM_INSTRUCTION
from app.memory import gemini_client
from app.tools import declarations
from google.genai import Client, types

from evals.metrics import (
    DEFAULT_TOLERANCE,
    markdown_table,
    regressions,
    summarize_tools,
    tool_call_matches,
)
from evals.run_eval import Pacer

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
BASELINE = HERE / "tool_baseline.json"


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def contents(case: dict) -> list[types.Content]:
    turns = [
        types.Content(role=role, parts=[types.Part(text=text)])
        for role, text in case.get("history", [])
    ]
    turns.append(types.Content(role="user", parts=[types.Part(text=case["say"])]))
    return turns


async def first_call(client: Client, model: str, case: dict) -> dict | None:
    res = await client.aio.models.generate_content(
        model=model,
        contents=contents(case),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[types.Tool(function_declarations=declarations())],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.0,
        ),
    )
    calls = res.function_calls or []
    return {"name": calls[0].name, "args": dict(calls[0].args or {})} if calls else None


async def run_case(case: dict, client: Client, model: str, pacer: Pacer) -> dict:
    result = {"id": case["id"], "group": case["group"], "say": case["say"], "ok": False}
    for attempt in range(3):
        await pacer.wait()
        try:
            call = await first_call(client, model, case)
            break
        except Exception as exc:  # quota or network: retry, then leave it out of the scores
            if attempt == 2:
                result["error"] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
                print(f"! {case['id']} {result['error']}")
                return result
            await asyncio.sleep(20)
    result["call"] = call
    result["expected"] = {"tool": case["tool"], "args": case.get("args", {})}
    result["ok"] = tool_call_matches(case["tool"], case.get("args", {}), call)
    got = (
        f"{call['name']}({json.dumps(call['args'], ensure_ascii=False)})" if call else "(araç yok)"
    )
    print(f"{'✓' if result['ok'] else '✗'} {case['id']} {case['say']} → {got}")
    return result


async def main() -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--model", default=s.agent_model)
    parser.add_argument("--limit", type=int, default=0, help="only the first N cases")
    parser.add_argument(
        "--rpm", type=float, default=12, help="requests per minute (free tier; 0 = no limit)"
    )
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--save-baseline", action="store_true")
    args = parser.parse_args()

    if not s.gemini_api_key:
        print("GEMINI_API_KEY is not set (backend/.env); the eval needs the real model.")
        return 2

    cases = load_cases(HERE / "tool_cases.jsonl")
    if args.limit:
        cases = cases[: args.limit]
    client, pacer = gemini_client(s), Pacer(args.rpm)
    started = time.monotonic()
    results = [await run_case(c, client, args.model, pacer) for c in cases]

    summary = summarize_tools(results)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else None
    run = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - started, 1),
        "config": {"model": args.model, "tools": len(declarations())},
        "summary": summary,
        "cases": results,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "tools-latest.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    table = markdown_table(summary, baseline["summary"] if baseline else None)
    misses = "\n".join(
        f"- {r['id']} «{r['say']}» → {r['call']['name'] if r.get('call') else '(araç yok)'}"
        f" (beklenen {r['expected']['tool']})"
        for r in results
        if not r["ok"] and not r.get("error")
    )
    (RESULTS / "tools-latest.md").write_text(
        f"# Tool routing eval, {run['time']}\n\nModel: {args.model}\n\n{table}\n\n"
        f"## Kaçanlar\n\n{misses or '(yok)'}\n",
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
