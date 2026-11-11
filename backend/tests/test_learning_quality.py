"""Experience lessons found by meaning, skill health and the tool-routing metric."""

import json
from types import SimpleNamespace

import numpy as np
from app import experience
from app.tools import skills
from evals.metrics import summarize_tools, tool_call_matches

from tests.test_skills import write_skill


class TopicEmbedder:
    """Maps texts to fixed topic vectors: messaging, installing, or other."""

    dim = 3

    def __init__(self):
        self.calls = 0

    async def embed(self, texts, *, query=False):
        self.calls += len(texts)
        rows = []
        for t in texts:
            t = t.lower()
            if "whatsapp" in t or "mesaj" in t or "yaz" in t:
                rows.append([1, 0, 0])
            elif "kur" in t or "winget" in t:
                rows.append([0, 1, 0])
            else:
                rows.append([0, 0, 1])
        return np.array(rows, dtype=np.float32)


def write_lessons(path, entries):
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8"
    )


async def test_lessons_found_by_meaning_and_cached(tmp_path):
    log = tmp_path / "experience.jsonl"
    write_lessons(
        log,
        [
            {"task": "AnyDesk'i kur", "ok": True, "lesson": "winget kimliği gerekti"},
            {"task": "Ahmet'e WhatsApp mesajı", "ok": False, "lesson": "pencere odaklanmadı"},
        ],
    )
    emb = TopicEmbedder()
    # "Mehmet'e yaz" shares no word with the WhatsApp lesson.
    section = await experience.prompt_section_for(log, "Mehmet'e yaz", emb)
    assert "pencere odaklanmadı" in section and "winget" not in section
    first = emb.calls
    await experience.prompt_section_for(log, "Ayşe'ye yaz", emb)
    assert emb.calls == first + 1  # only the query; lesson vectors came from the cache


async def test_lessons_fall_back_to_word_overlap(tmp_path):
    log = tmp_path / "experience.jsonl"
    write_lessons(log, [{"task": "AnyDesk kurulumu", "ok": True, "lesson": "winget kimliği"}])

    class Broken:
        dim = 3

        async def embed(self, texts, *, query=False):
            raise RuntimeError("no network")

    section = await experience.prompt_section_for(log, "anydesk kurulumu yap", Broken())
    assert "winget" in section


async def test_failure_streak_offers_repair(monkeypatch, settings):
    write_skill(settings.skills_dir)
    out = {"text": "Hata: pencere yok"}
    monkeypatch.setattr(
        skills,
        "_execute",
        lambda s, a: SimpleNamespace(
            returncode=int(out["text"].startswith("Hata")), stdout=out["text"] + "\n", stderr=""
        ),
    )
    call = {"name": "selam", "args_json": '{"ad": "a"}'}
    for _ in range(2):
        assert "needs_repair" not in await skills.run_skill(call, settings)
    third = await skills.run_skill(call, settings)
    assert third["needs_repair"] and "onarmasını" in third["message"]

    listed = await skills.list_skills({}, settings)
    assert "hep başarısız" in listed["message"] and listed["health"]["selam"]["fails"] == 3

    out["text"] = "Selam söylendi."
    assert (await skills.run_skill(call, settings))["status"] == "ok"
    assert skills.load_health(settings.data_dir / skills.HEALTH_FILE)["selam"]["streak"] == 0


def test_tool_call_matching():
    call = {"name": "get_weather", "args": {"city": "İzmir"}}
    assert tool_call_matches("get_weather", {"city": "izmir"}, call)
    assert not tool_call_matches("get_weather", {"city": "ankara"}, call)
    assert not tool_call_matches("open_program", {}, call)
    assert tool_call_matches(None, {}, None)
    assert not tool_call_matches(None, {}, call)
    assert tool_call_matches(["reply_email", "get_weather"], {"body": "x"}, call)
    assert tool_call_matches(
        "set_volume", {"level": 30}, {"name": "set_volume", "args": {"level": 30}}
    )


def test_tool_summary_by_group():
    s = summarize_tools(
        [
            {"id": "1", "group": "a", "ok": True},
            {"id": "2", "group": "a", "ok": False},
            {"id": "3", "group": "b", "ok": True},
            {"id": "4", "group": "b", "ok": False, "error": "429"},
        ]
    )
    assert s["tool_accuracy"] == 0.667 and s["tool_accuracy_a"] == 0.5 and s["errors"] == 1


def test_tool_cases_name_real_tools():
    from pathlib import Path

    from app.tools import TOOLS

    path = Path(__file__).resolve().parent.parent / "evals" / "tool_cases.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        tools = case["tool"] if isinstance(case["tool"], list) else [case["tool"]]
        assert all(t is None or t in TOOLS for t in tools), case["id"]
