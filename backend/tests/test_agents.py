"""The ADK memory researcher with a scripted model: no network, real ADK runner."""

from app.agents import MemoryResearcher
from app.agents.researcher import MAX_SEARCHES

from tests.memory_fakes import ScriptedLlm, fake_memory


async def memory_with_facts():
    memory = fake_memory()
    await memory.remember("Kira her ayın 5'inde ödenir.")
    await memory.remember("Spor salonu dolabının numarası 112.")
    return memory


async def test_answer_keeps_only_citations_it_retrieved():
    memory = await memory_with_facts()
    llm = ScriptedLlm(
        turns=[
            [("search_memory", {"query": "kira ne zaman ödenir"})],
            "Kira ayın 5'inde ödenir [{0}]. Depozito 65.000 TL [uydurma.md#2].",
        ],
        requests=[],
    )
    res = await MemoryResearcher(memory, llm, top_k=1).ask("Kira ne zaman?")

    assert res.found
    assert res.retrieved == ["not-1"]
    assert res.cited == ["not-1"]
    assert res.dropped == ["uydurma.md#2"]
    assert res.answer == "Kira ayın 5'inde ödenir. Depozito 65.000 TL."
    assert res.sources == [{"id": "not-1", "title": "Not"}]
    assert res.searches == 1
    # The tool declaration the model saw:
    decl = llm.requests[0].config.tools[0].function_declarations[0]
    assert decl.name == "search_memory"


async def test_invented_citations_alone_mean_not_found():
    memory = await memory_with_facts()
    llm = ScriptedLlm(
        turns=[
            [("search_memory", {"query": "netflix şifresi"})],
            "Netflix şifren 1234 [sifreler.txt#0].",
        ],
        requests=[],
    )
    res = await MemoryResearcher(memory, llm).ask("Netflix şifrem ne?")
    assert not res.found
    assert res.answer == ""
    assert res.dropped == ["sifreler.txt#0"]


async def test_model_saying_not_found_is_not_found():
    memory = await memory_with_facts()
    llm = ScriptedLlm(
        turns=[[("search_memory", {"query": "kardeşimin doğum günü"})], "BULUNAMADI"],
        requests=[],
    )
    res = await MemoryResearcher(memory, llm).ask("Kardeşimin doğum günü?")
    assert not res.found and res.cited == []


async def test_guardrail_caps_searches_and_blocks_unknown_tools():
    memory = await memory_with_facts()
    searches = [[("search_memory", {"query": f"deneme {i}"})] for i in range(MAX_SEARCHES + 1)]
    llm = ScriptedLlm(
        turns=[*searches, [("delete_everything", {})], "Dolap 112 [{0}]."],
        requests=[],
    )
    res = await MemoryResearcher(memory, llm).ask("Dolap numaram?")

    assert res.searches == MAX_SEARCHES
    responses = [
        part.function_response.response
        for req in llm.requests
        for content in req.contents
        for part in content.parts or []
        if part.function_response
    ]
    # The extra search was refused without running; the unknown tool never ran.
    assert any("Arama hakkın bitti" in str(r) for r in responses)
    assert res.found and res.cited
