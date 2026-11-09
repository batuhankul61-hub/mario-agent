"""Memory tools the Live model calls: remember, recall, search_documents,
ask_memory (the researcher agent), index_documents, index_emails, forget_memory.

The memory and the researcher are built on first use, so a server without a
Gemini key still starts and every other tool keeps working."""

from typing import TYPE_CHECKING

from ..agents import researcher_from_settings
from ..memory import Hit, Memory, gemini_client, memory_from_settings
from . import gmail

if TYPE_CHECKING:
    from . import Context

SNIPPET = 400
NO_KEY = {"status": "error", "message": "Hafıza için sunucuda GEMINI_API_KEY gerekli."}


def _memory(ctx: "Context") -> Memory | None:
    if ctx.memory is None:
        if not ctx.settings.gemini_api_key:
            return None
        ctx.memory = memory_from_settings(ctx.settings, gemini_client(ctx.settings))
    return ctx.memory


def _hits(hits: list[Hit]) -> list[dict]:
    return [
        {
            "id": h.record.id,
            "kind": h.record.kind,
            "title": h.record.title,
            "score": round(h.score, 3),
            "text": h.record.text[:SNIPPET] + ("…" if len(h.record.text) > SNIPPET else ""),
        }
        for h in hits
    ]


async def remember(args: dict, ctx: "Context") -> dict:
    text = (args.get("text") or "").strip()
    if not text:
        return {"status": "error", "message": "Neyi hatırlayayım?"}
    if (memory := _memory(ctx)) is None:
        return NO_KEY
    record = await memory.remember(text)
    return {"status": "ok", "message": f"Not aldım ({record.id}).", "id": record.id}


async def _search(args: dict, ctx: "Context", kinds: set[str] | None) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"status": "error", "message": "Neyi arayayım?"}
    if (memory := _memory(ctx)) is None:
        return NO_KEY
    hits = await memory.search(query, k=ctx.settings.memory_top_k, kinds=kinds)
    if not hits:
        return {"status": "ok", "message": "Hafızada bununla ilgili bir şey yok.", "results": []}
    result = {
        "status": "ok",
        "message": f"{len(hits)} parça bulundu. Cevap verirken kaynağı (title) söyle.",
        "results": _hits(hits),
    }
    if any(h.record.kind == "email" for h in hits):
        result["untrusted_content"] = True
    return result


async def recall(args: dict, ctx: "Context") -> dict:
    return await _search(args, ctx, None)


async def search_documents(args: dict, ctx: "Context") -> dict:
    return await _search(args, ctx, {"document"})


async def ask_memory(args: dict, ctx: "Context") -> dict:
    question = (args.get("question") or "").strip()
    if not question:
        return {"status": "error", "message": "Soru ne?"}
    if (memory := _memory(ctx)) is None:
        return NO_KEY
    if ctx.researcher is None:
        ctx.researcher = researcher_from_settings(ctx.settings, memory, gemini_client(ctx.settings))
    res = await ctx.researcher.ask(question)
    if not res.found:
        return {
            "status": "ok",
            "found": False,
            "message": "Hafızamda bununla ilgili kaynaklı bir bilgi bulamadım. Tahmin yürütme.",
        }
    titles = list(dict.fromkeys(s["title"] for s in res.sources))
    return {
        "status": "ok",
        "found": True,
        "message": f"{res.answer} (Kaynak: {', '.join(titles)})",
        "answer": res.answer,
        "sources": res.sources,
    }


async def index_documents(args: dict, ctx: "Context") -> dict:
    folder = ctx.settings.memory_docs_dir
    if not folder.is_dir():
        return {"status": "error", "message": f"Belge klasörü yok: {folder}"}
    if (memory := _memory(ctx)) is None:
        return NO_KEY
    rep = await memory.index_folder(folder)
    parts = [f"{len(rep.added)} belge eklendi ya da güncellendi ({rep.chunks} parça)"]
    if rep.unchanged:
        parts.append(f"{rep.unchanged} belge değişmemiş")
    if rep.removed:
        parts.append(f"{len(rep.removed)} silinen belge hafızadan çıkarıldı")
    if rep.failed:
        parts.append(f"okunamayanlar: {', '.join(rep.failed)}")
    return {"status": "ok", "message": "; ".join(parts) + ".", "added": rep.added}


async def index_emails(args: dict, ctx: "Context") -> dict:
    query = (args.get("query") or "in:inbox newer_than:7d").strip()
    limit = max(1, min(50, int(args.get("max_results") or 20)))
    if (memory := _memory(ctx)) is None:
        return NO_KEY
    fetched = await gmail.fetch_messages(ctx.settings, query, limit)
    if fetched.get("status") != "ok":
        return fetched
    messages = fetched["messages"]
    chunks = await memory.index_emails(messages)
    return {
        "status": "ok",
        "message": f"{len(messages)} e-posta hafızaya eklendi ({chunks} parça).",
    }


async def forget_memory(args: dict, ctx: "Context") -> dict:
    note_id = (args.get("id") or "").strip()
    if (memory := _memory(ctx)) is None:
        return NO_KEY
    if not memory.forget(note_id):
        return {"status": "error", "message": f"{note_id} diye bir not yok."}
    return {"status": "ok", "message": f"{note_id} notunu unuttum."}


def forget_question(args: dict, ctx: "Context") -> str:
    note_id = (args.get("id") or "").strip()
    memory = _memory(ctx)
    record = memory.store.get(note_id) if memory else None
    if record is None:
        return f"{note_id} notu silinsin mi?"
    text = record.text if len(record.text) <= 120 else record.text[:120] + "…"
    return f"Şu not silinsin mi: «{text}»"
