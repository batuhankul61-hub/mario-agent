"""The confirmation flow through /api/tool, with the real shutdown swapped out."""

import json

import pytest
from app import audit
from app.tools import TOOLS, Tool


@pytest.fixture
def shutdowns(monkeypatch):
    """Replaces shutdown_computer's action with a recorder."""
    calls = []

    async def fake(args, ctx):
        calls.append(args)
        return {"status": "ok", "message": "Bilgisayar 30 saniye içinde kapatılacak."}

    original = TOOLS["shutdown_computer"]
    monkeypatch.setitem(
        TOOLS,
        "shutdown_computer",
        Tool(
            original.name,
            original.description,
            fake,
            original.properties,
            original.required,
            original.confirm,
        ),
    )
    return calls


def ask_shutdown(client):
    res = client.post(
        "/api/tool", json={"name": "shutdown_computer", "args": {"mode": "shutdown"}}
    ).json()
    assert res["status"] == "needs_confirmation"
    return res


def test_destructive_tool_only_parks_the_action(client, shutdowns):
    res = ask_shutdown(client)
    assert res["question"] == "Bilgisayar kapatılsın mı?"
    assert res["confirmation_id"]
    assert shutdowns == []


def test_confirm_with_spoken_yes_runs_it_once(client, shutdowns, settings):
    cid = ask_shutdown(client)["confirmation_id"]
    body = {
        "name": "confirm_action",
        "args": {"confirmation_id": cid},
        "utterances": ["Evet, eminim."],
    }

    first = client.post("/api/tool", json=body).json()
    second = client.post("/api/tool", json=body).json()

    assert first["status"] == "ok"
    assert shutdowns == [{"mode": "shutdown"}]
    assert second["status"] == "error"  # the confirmation is single-use
    entries = audit.tail(settings.audit_log)
    assert [e["status"] for e in entries] == ["needs_confirmation", "ok"]
    assert entries[-1]["confirmed_by"] == "voice: Evet, eminim."


@pytest.mark.parametrize("utterances", [[], ["hayır"], ["bilgisayarı kapat"], ["tamam yapma"]])
def test_model_cannot_confirm_without_the_users_yes(client, shutdowns, settings, utterances):
    cid = ask_shutdown(client)["confirmation_id"]
    res = client.post(
        "/api/tool",
        json={"name": "confirm_action", "args": {"confirmation_id": cid}, "utterances": utterances},
    ).json()

    assert res["status"] == "not_confirmed"
    assert shutdowns == []
    assert audit.tail(settings.audit_log)[-1]["status"] == "not_confirmed"


def test_not_confirmed_can_be_retried(client, shutdowns):
    cid = ask_shutdown(client)["confirmation_id"]
    call = {"name": "confirm_action", "args": {"confirmation_id": cid}}
    assert (
        client.post("/api/tool", json={**call, "utterances": ["hmm"]}).json()["status"]
        == "not_confirmed"
    )
    assert client.post("/api/tool", json={**call, "utterances": ["evet"]}).json()["status"] == "ok"
    assert len(shutdowns) == 1


def test_unknown_confirmation_id(client, shutdowns):
    res = client.post(
        "/api/tool",
        json={
            "name": "confirm_action",
            "args": {"confirmation_id": "nope"},
            "utterances": ["evet"],
        },
    ).json()
    assert res["status"] == "error"
    assert shutdowns == []


def test_expired_confirmation(client, shutdowns, pending):
    pending.ttl = 0
    cid = ask_shutdown(client)["confirmation_id"]
    res = client.post(
        "/api/tool",
        json={"name": "confirm_action", "args": {"confirmation_id": cid}, "utterances": ["evet"]},
    ).json()
    assert res["status"] == "error"
    assert shutdowns == []


def test_cancel_action(client, shutdowns, settings):
    cid = ask_shutdown(client)["confirmation_id"]
    res = client.post(
        "/api/tool", json={"name": "cancel_action", "args": {"confirmation_id": cid}}
    ).json()
    assert res["message"] == "İş iptal edildi."
    late = client.post(
        "/api/tool",
        json={"name": "confirm_action", "args": {"confirmation_id": cid}, "utterances": ["evet"]},
    ).json()
    assert late["status"] == "error"
    assert shutdowns == []
    assert audit.tail(settings.audit_log)[-1]["status"] == "cancelled"


def test_button_approval(client, shutdowns, settings):
    cid = ask_shutdown(client)["confirmation_id"]
    res = client.post(f"/api/confirm/{cid}", json={"approved": True}).json()
    assert res["status"] == "ok"
    assert res["tool"] == "shutdown_computer"
    assert shutdowns == [{"mode": "shutdown"}]
    assert audit.tail(settings.audit_log)[-1]["confirmed_by"] == "button"
    assert client.post(f"/api/confirm/{cid}", json={"approved": True}).status_code == 404


def test_button_cancel(client, shutdowns):
    cid = ask_shutdown(client)["confirmation_id"]
    assert (
        client.post(f"/api/confirm/{cid}", json={"approved": False}).json()["message"]
        == "İş iptal edildi."
    )
    assert shutdowns == []


def test_unknown_tool(client):
    res = client.post("/api/tool", json={"name": "format_disk", "args": {}}).json()
    assert res == {"status": "error", "message": "Bilinmeyen araç: format_disk."}


def test_tool_crash_becomes_error_result(client, monkeypatch, settings):
    async def broken(args, ctx):
        raise ValueError("bad")

    monkeypatch.setitem(TOOLS, "get_time", Tool("get_time", "", broken))
    res = client.post("/api/tool", json={"name": "get_time"}).json()
    assert res["status"] == "error"
    assert audit.tail(settings.audit_log)[-1]["tool"] == "get_time"


def test_every_call_is_audited(client, settings):
    client.post("/api/tool", json={"name": "get_time"})
    entry = audit.tail(settings.audit_log)[-1]
    assert entry["tool"] == "get_time" and entry["status"] == "ok"
    assert client.get("/api/audit").json()[-1]["tool"] == "get_time"


def test_audit_keeps_turkish_readable(settings):
    audit.record(settings.audit_log, tool="x", message="Bilgisayar kapatılacak")
    assert "kapatılacak" in settings.audit_log.read_text(encoding="utf-8")
    assert (
        json.loads(settings.audit_log.read_text(encoding="utf-8"))["message"]
        == "Bilgisayar kapatılacak"
    )


def test_screenshot_route_rejects_other_paths(client):
    assert client.get("/api/screenshots/..%5C..%5Cwin.ini").status_code == 404
    assert client.get("/api/screenshots/passwords.png").status_code == 404
