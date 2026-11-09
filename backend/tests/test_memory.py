"""Memory and RAG pieces, offline: chunking, vector search, indexing, citations."""

import numpy as np
import pytest
from app.memory import Record, VectorStore, check_citations
from app.memory.chunking import chunk_text
from app.memory.loaders import find_documents, read_document

from tests.memory_fakes import DIM, fake_memory

# ---------- chunking ----------


def test_short_text_is_one_chunk():
    chunks = chunk_text("Kira ayın 5'inde ödenir.", size=100, overlap=20)
    assert [c.text for c in chunks] == ["Kira ayın 5'inde ödenir."]


def test_chunks_respect_size_and_cover_every_sentence():
    sentences = [f"Cümle {i} biraz uzun bir içerik taşıyor." for i in range(40)]
    text = " ".join(sentences)
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert all(s in joined for s in sentences)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_next_chunk_starts_with_the_overlap():
    text = " ".join(f"Cümle numarası {i} burada." for i in range(30))
    chunks = chunk_text(text, size=120, overlap=40)
    for prev, nxt in zip(chunks, chunks[1:]):
        last_sentence = prev.text.splitlines()[-1]
        assert nxt.text.startswith(last_sentence)


def test_paragraphs_are_kept_whole_and_headings_travel_with_chunks():
    text = "# Kira\n\n## Ödeme\n\nKira 32.500 TL.\n\nAyın 5'ine kadar.\n\n## Bakım\n\nKombi ev sahibinde."
    chunks = chunk_text(text, size=40, overlap=0)

    def heading_of(fact: str) -> str:
        return next(c.heading for c in chunks if fact in c.text)

    assert len(chunks) > 1
    assert heading_of("Kombi").endswith("Bakım")
    assert "Ödeme" in heading_of("32.500")
    assert "Bakım" not in heading_of("32.500")
    assert all("\n\n" not in c.text for c in chunks)


def test_a_sentence_longer_than_a_chunk_is_cut_at_spaces():
    text = " ".join(["kelime"] * 100)
    chunks = chunk_text(text, size=60, overlap=0)
    assert all(len(c.text) <= 60 for c in chunks)
    assert sum(c.text.count("kelime") for c in chunks) == 100


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (100, 100), (100, -1)])
def test_bad_chunk_settings(size, overlap):
    with pytest.raises(ValueError):
        chunk_text("x", size=size, overlap=overlap)


# ---------- vector store ----------


def unit(*values: float) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[: len(values)] = values
    return v / np.linalg.norm(v)


def rec(i: str, kind: str = "document") -> Record:
    return Record(id=i, source=i.split("#")[0], kind=kind, title=i, text=f"metin {i}")


def test_search_ranks_by_cosine_similarity():
    store = VectorStore(":memory:", DIM)
    store.add(
        [rec("a.md#0"), rec("b.md#0"), rec("c.md#0")],
        np.vstack([unit(1, 0), unit(0.6, 0.8), unit(0, 1)]),
    )
    hits = store.search(unit(1, 0.1), k=3)
    assert [h.record.id for h in hits] == ["a.md#0", "b.md#0", "c.md#0"]
    assert hits[0].score == pytest.approx(float(unit(1, 0) @ unit(1, 0.1)))
    assert [h.record.id for h in store.search(unit(0, 1), k=1)] == ["c.md#0"]


def test_search_filters_by_kind_and_handles_small_stores():
    store = VectorStore(":memory:", DIM)
    assert store.search(unit(1), k=3) == []
    store.add([rec("a.md#0"), rec("not-1", "note")], np.vstack([unit(1), unit(1, 0.1)]))
    assert [h.record.id for h in store.search(unit(1), k=10, kinds={"note"})] == ["not-1"]
    assert len(store.search(unit(1), k=10)) == 2
    assert [h.record.id for h in store.search(unit(0, 1), k=5, min_score=0.05)] == ["not-1"]


def test_ties_keep_insertion_order():
    store = VectorStore(":memory:", DIM)
    store.add([rec("x#0"), rec("y#0"), rec("z#0")], np.vstack([unit(1)] * 3))
    assert [h.record.id for h in store.search(unit(1), k=3)] == ["x#0", "y#0", "z#0"]


def test_store_persists_and_rejects_wrong_dimensions(tmp_path):
    path = tmp_path / "m.sqlite"
    store = VectorStore(path, DIM)
    store.add([rec("a.md#0")], unit(1).reshape(1, -1))
    with pytest.raises(ValueError):
        store.add([rec("b.md#0")], np.ones((1, DIM + 1), dtype=np.float32))
    store.close()
    again = VectorStore(path, DIM)
    assert again.search(unit(1), k=1)[0].record.id == "a.md#0"
    assert again.delete_source("a.md") == 1
    assert again.search(unit(1), k=1) == []


# ---------- memory service ----------


async def test_remember_assigns_note_ids_and_finds_them():
    memory = fake_memory()
    first = await memory.remember("Annemin doğum günü 17 Mart.")
    second = await memory.remember("Dolap numaram 112.")
    assert (first.id, second.id) == ("not-1", "not-2")
    hits = await memory.search("annemin doğum günü ne zaman", k=1)
    assert hits[0].record.id == "not-1"
    assert memory.stats() == {"note": 2, "document": 0, "email": 0}


async def test_forget_only_removes_notes():
    memory = fake_memory()
    note = await memory.remember("Geçici not.")
    memory.store.add([rec("a.md#0")], unit(1).reshape(1, -1))
    assert not memory.forget("a.md#0")
    assert not memory.forget("not-99")
    assert memory.forget(note.id)
    assert memory.stats()["note"] == 0


async def test_index_folder_embeds_only_new_and_changed_files(tmp_path):
    docs = tmp_path / "notes"
    (docs / "alt").mkdir(parents=True)
    (docs / "kira.md").write_text("# Kira\n\nKira ayın 5'inde ödenir.", encoding="utf-8")
    (docs / "alt" / "araba.txt").write_text("Arabanın bakımı haziranda.", encoding="utf-8")
    (docs / "README.md").write_text("Bu klasöre belge koy.", encoding="utf-8")
    (docs / "_arsiv").mkdir()
    (docs / "_arsiv" / "eski.md").write_text("eski", encoding="utf-8")
    (docs / "resim.png").write_bytes(b"\x89PNG")
    memory = fake_memory()

    first = await memory.index_folder(docs)
    assert sorted(first.added) == ["alt/araba.txt", "kira.md"]
    hits = await memory.search("arabanın bakımı", k=1)
    assert hits[0].record.id == "alt/araba.txt#0"
    assert hits[0].record.title == "Belge: alt/araba.txt"

    calls = len(memory.embedder.calls)
    again = await memory.index_folder(docs)
    assert (again.added, again.unchanged) == ([], 2)
    assert len(memory.embedder.calls) == calls  # nothing re-embedded

    (docs / "kira.md").write_text("Kira artık ayın 10'unda.", encoding="utf-8")
    (docs / "alt" / "araba.txt").unlink()
    third = await memory.index_folder(docs)
    assert (third.added, third.removed) == (["kira.md"], ["alt/araba.txt"])
    texts = [h.record.text for h in await memory.search("kira", k=5)]
    assert texts == ["Kira artık ayın 10'unda."]


async def test_index_emails_replaces_a_message_on_reindex():
    memory = fake_memory()
    mail = {"id": "m1", "from": "Ahmet", "subject": "Toplantı", "date": "", "body": "Yarın 10'da."}
    assert await memory.index_emails([mail]) == 1
    assert await memory.index_emails([{**mail, "body": "Yarın 11'de."}]) == 1
    hits = await memory.search("toplantı", k=5, kinds={"email"})
    assert [(h.record.id, h.record.text) for h in hits] == [("mail-m1#0", "Yarın 11'de.")]


def minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


def test_pdf_text_is_extracted(tmp_path):
    path = tmp_path / "police.pdf"
    path.write_bytes(minimal_pdf("Policy ends on 3 March 2027"))
    assert find_documents(tmp_path) == [path]
    assert "Policy ends on 3 March 2027" in read_document(path)


# ---------- citations ----------


def test_citations_outside_the_retrieved_chunks_are_dropped():
    checked = check_citations(
        "Kira 32.500 TL [kira.md#0]. Depozito 65.000 TL [uydurma.md#3]. Aidat 1.800 TL [kira.md#0, not-9].",
        {"kira.md#0", "not-1"},
    )
    assert checked.cited == ["kira.md#0"]
    assert checked.dropped == ["uydurma.md#3", "not-9"]
    assert checked.text == "Kira 32.500 TL. Depozito 65.000 TL. Aidat 1.800 TL."
    assert checked.grounded


def test_answer_without_valid_citations_is_not_grounded():
    assert not check_citations("Kira 50.000 TL [hayal.md#0].", {"kira.md#0"}).grounded
    assert not check_citations("Kira 50.000 TL.", {"kira.md#0"}).grounded
