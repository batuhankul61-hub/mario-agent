"""Learning: learn_skill asks first, Claude writes skills/<name>/, run_skill runs it."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from app import audit
from app.gemini import GeminiService
from app.tools import claude, skills

PS_SCRIPT = '﻿# test\n$a = [Console]::In.ReadToEnd() | ConvertFrom-Json\n"Merhaba $($a.ad)"\n'


def write_skill(skills_dir, name="selam", confirm=None, entry="run.py", body=None, **manifest):
    folder = skills_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "description": "Birine selam söyler.",
        "parameters": {"ad": "Kişinin adı"},
        "required": ["ad"],
        "confirm": confirm,
        "entry": entry,
        "example": "Ahmet'e selam söyle",
    } | manifest
    (folder / "skill.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if "/" in entry or "\\" in entry:
        return folder  # path-escape cases: the loader must reject them, there is nothing to write
    if body is None:
        body = (
            "import json, sys\nsys.stdout.reconfigure(encoding='utf-8')\nargs = json.load(sys.stdin)\n"
            "if args.get('ad') == 'hata':\n    print('Hata: kişi bulunamadı'); sys.exit(1)\n"
            "print(f\"Selam söylendi: {args['ad']}\")\n"
        )
    (folder / entry).write_text(body, encoding="utf-8")
    return folder


# ---------- loading ----------


def test_loads_a_valid_skill(settings):
    write_skill(settings.skills_dir)
    skill = skills.load_skills(settings.skills_dir)["selam"]
    assert skill.signature() == "- selam(ad: Kişinin adı): Birine selam söyler."


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"name": "Baska"}, "klasör adıyla"),
        ({"description": ""}, "description"),
        ({"required": ["yas"]}, "required"),
        ({"entry": "../../backend/app/main.py"}, "entry"),
        ({"entry": "run.exe"}, "entry"),
        ({"parameters": ["ad"]}, "parameters"),
    ],
)
def test_rejects_bad_manifests(settings, change, reason):
    folder = write_skill(settings.skills_dir, **change)
    with pytest.raises(skills.InvalidSkill, match=reason):
        skills.load_skill(folder)
    assert skills.load_skills(settings.skills_dir) == {}  # skipped, never crashes Mario


def test_ignores_forgotten_and_hidden_folders(settings):
    write_skill(settings.skills_dir / skills.FORGOTTEN, name="eski")
    assert skills.load_skills(settings.skills_dir) == {}


def test_confirm_template_only_substitutes_plain_names(settings):
    write_skill(settings.skills_dir, confirm="{ad} için {ad.__class__} gönderilsin mi?")
    skill = skills.load_skills(settings.skills_dir)["selam"]
    assert skill.question({"ad": "Ahmet"}) == "Ahmet için {ad.__class__} gönderilsin mi?"


def test_prompt_lists_learned_skills(settings):
    assert skills.prompt_section(settings.skills_dir) == ""
    write_skill(settings.skills_dir, confirm="{ad} kişisine selam gönderilsin mi?")
    section = GeminiService(settings).live_config().system_instruction
    assert "- selam(ad: Kişinin adı): Birine selam söyler. (onay ister)" in section


# ---------- running ----------


async def test_run_python_skill(settings):
    write_skill(settings.skills_dir)
    res = await skills.run_skill({"name": "selam", "args_json": '{"ad": "Ayşe"}'}, settings)
    assert res == {"status": "ok", "message": "Selam söylendi: Ayşe"}


async def test_run_powershell_skill(settings):
    write_skill(settings.skills_dir, entry="run.ps1", body=PS_SCRIPT)
    res = await skills.run_skill({"name": "selam", "args_json": '{"ad": "Çağrı"}'}, settings)
    assert res == {"status": "ok", "message": "Merhaba Çağrı"}


async def test_script_error_becomes_spoken_error(settings):
    write_skill(settings.skills_dir)
    res = await skills.run_skill({"name": "selam", "args_json": '{"ad": "hata"}'}, settings)
    assert res == {"status": "error", "message": "selam: kişi bulunamadı"}


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"name": "yok_boyle"}, "'yok_boyle' adında öğrenilmiş bir yetenek yok."),
        ({"name": "selam", "args_json": "{bozuk"}, "args_json geçerli bir JSON nesnesi değil."),
        ({"name": "selam", "args_json": "[1]"}, "args_json bir JSON nesnesi olmalı."),
        ({"name": "selam", "args_json": "{}"}, "Eksik bilgi: ad."),
    ],
)
async def test_bad_calls(settings, args, message):
    write_skill(settings.skills_dir)
    assert await skills.run_skill(args, settings) == {"status": "error", "message": message}


async def test_timeout(settings, monkeypatch):
    write_skill(settings.skills_dir)

    def slow(skill, args):
        raise skills.subprocess.TimeoutExpired("x", skills.RUN_TIMEOUT)

    monkeypatch.setattr(skills, "_execute", slow)
    res = await skills.run_skill({"name": "selam", "args_json": '{"ad": "a"}'}, settings)
    assert res["message"] == "selam 30 saniyede bitmedi."


def test_destructive_skill_waits_for_voice_yes(client, settings):
    write_skill(settings.skills_dir, confirm="{ad} kişisine selam gönderilsin mi?")
    call = {"name": "run_skill", "args": {"name": "selam", "args_json": '{"ad": "Ahmet"}'}}
    ask = client.post("/api/tool", json=call).json()
    assert ask["status"] == "needs_confirmation"
    assert ask["question"] == "Ahmet kişisine selam gönderilsin mi?"

    done = client.post(
        "/api/tool",
        json={
            "name": "confirm_action",
            "args": {"confirmation_id": ask["confirmation_id"]},
            "utterances": ["evet"],
        },
    ).json()
    assert done == {"status": "ok", "message": "Selam söylendi: Ahmet"}


def test_harmless_skill_runs_without_asking(client, settings):
    write_skill(settings.skills_dir)
    res = client.post(
        "/api/tool",
        json={"name": "run_skill", "args": {"name": "selam", "args_json": '{"ad": "Ali"}'}},
    )
    assert res.json()["status"] == "ok"


async def test_forget_moves_instead_of_deleting(settings):
    write_skill(settings.skills_dir)
    res = await skills.forget_skill({"name": "selam"}, settings)
    assert res["status"] == "ok"
    assert skills.load_skills(settings.skills_dir) == {}
    kept = list((settings.skills_dir / skills.FORGOTTEN).iterdir())
    assert len(kept) == 1 and (kept[0] / "skill.json").exists()


async def test_list_skills(settings):
    assert (await skills.list_skills({}, settings))[
        "message"
    ] == "Henüz öğrendiğim bir yetenek yok."
    write_skill(settings.skills_dir)
    assert (await skills.list_skills({}, settings))[
        "message"
    ] == "Öğrendiğim yetenekler: selam: Birine selam söyler."


# ---------- learning through Claude Code ----------


@pytest.fixture
def jobs(settings, tmp_path):
    return claude.ClaudeJobs(
        "claude",
        tmp_path / "work",
        1,
        settings.audit_log,
        project_dir=tmp_path / "project",
        skills_dir=settings.skills_dir,
    )


def fake_claude(monkeypatch, settings, answer, write=True):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        if write:
            write_skill(settings.skills_dir, name="doviz_kuru")
        return SimpleNamespace(
            returncode=0, stdout=json.dumps({"is_error": False, "result": answer}), stderr=""
        )

    monkeypatch.setattr(claude.subprocess, "run", run)
    return seen


async def wait(jobs, job_id):
    for _ in range(200):
        if jobs.jobs[job_id].state != "running":
            return jobs.jobs[job_id]
        await asyncio.sleep(0.01)
    raise AssertionError("job never finished")


async def test_learning_produces_a_usable_skill(jobs, monkeypatch, settings, tmp_path):
    seen = fake_claude(
        monkeypatch, settings, "**SONUÇ:** Döviz kurunu söylemeyi öğrendim.\nYETENEK: `doviz_kuru`"
    )
    started = await jobs.learn("dolar kaç TL")
    assert started["kind"] == "skill" and "Öğrenmeye başladım" in started["message"]

    job = await wait(jobs, started["job_id"])

    assert job.state == "done"
    assert job.result == "Döviz kurunu söylemeyi öğrendim."
    assert job.skill["name"] == "doviz_kuru" and job.skill["parameters"] == {"ad": "Kişinin adı"}
    assert seen["cwd"] == tmp_path / "project"  # where .claude/skills/mario-yetenek lives
    assert "«dolar kaç TL»" in seen["input"] and "mario-yetenek" in seen["input"]
    assert "Skill" in seen["cmd"] and "Edit(./backend/**)" in seen["cmd"]
    status = await jobs.status(job.id)
    assert status["message"] == "Öğrenme bitti. Döviz kurunu söylemeyi öğrendim."
    assert audit.tail(settings.audit_log)[-1]["skill"] == "doviz_kuru"


async def test_claude_says_it_cannot_teach(jobs, monkeypatch, settings):
    fake_claude(
        monkeypatch, settings, "SONUÇ: WhatsApp Desktop kurulu değil.\nYETENEK: yok", write=False
    )
    job = await wait(jobs, (await jobs.learn("whatsapp'tan yaz"))["job_id"])
    assert (
        job.state == "failed"
        and job.result == "WhatsApp Desktop kurulu değil."
        and job.skill is None
    )


async def test_claimed_skill_that_does_not_load_is_a_failure(jobs, monkeypatch, settings):
    fake_claude(monkeypatch, settings, "SONUÇ: Öğrendim.\nYETENEK: hayali_yetenek")
    job = await wait(jobs, (await jobs.learn("x"))["job_id"])
    assert job.state == "failed" and "kullanılamıyor" in job.result


def test_learn_skill_asks_permission_first(client, ctx):
    ask = client.post(
        "/api/tool", json={"name": "learn_skill", "args": {"request": "WhatsApp'tan Ahmet'e yaz"}}
    ).json()
    assert ask["status"] == "needs_confirmation"
    assert ask["question"] == (
        "Bunu henüz bilmiyorum. İzin verirsen Claude sayesinde öğreneyim: «WhatsApp'tan Ahmet'e yaz». Öğreneyim mi?"
    )
    assert ctx.jobs.jobs == {}


def test_learn_then_use_in_the_same_session(client, ctx, settings, monkeypatch):
    """The whole loop through the API: ask, yes, Claude writes the skill, run_skill uses it."""
    ctx.jobs.skills_dir = settings.skills_dir
    fake_claude(monkeypatch, settings, "SONUÇ: Öğrendim.\nYETENEK: doviz_kuru")

    ask = client.post(
        "/api/tool", json={"name": "learn_skill", "args": {"request": "dolar kaç"}}
    ).json()
    started = client.post(
        "/api/tool",
        json={
            "name": "confirm_action",
            "args": {"confirmation_id": ask["confirmation_id"]},
            "utterances": ["evet öğren"],
        },
    ).json()
    for _ in range(100):
        job = client.get(f"/api/jobs/{started['job_id']}").json()
        if job["state"] != "running":
            break
        time.sleep(0.02)
    assert (
        job["state"] == "done" and job["kind"] == "skill" and job["skill"]["name"] == "doviz_kuru"
    )

    used = client.post(
        "/api/tool",
        json={"name": "run_skill", "args": {"name": "doviz_kuru", "args_json": '{"ad": "x"}'}},
    )
    assert used.json()["status"] == "ok"
