"""The commands themselves, without touching Windows or the network."""

import asyncio
import datetime as dt
import json
import subprocess
import time
from types import SimpleNamespace

import httpx
import pytest
from app import audit
from app.tools import claude, system, web

# ---------- open_program ----------


@pytest.fixture
def programs(settings, monkeypatch):
    settings.programs_dir.mkdir(parents=True)
    for name in (
        "Flo Desk.lnk",
        "Google Chrome.lnk",
        "Chrome Remote Desktop.lnk",
        "Uninstall Chrome.lnk",
    ):
        (settings.programs_dir / name).write_text("")
    # Keep the real Start Menu out of the tests.
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    opened = []
    monkeypatch.setattr(system.os, "startfile", opened.append, raising=False)
    return opened


def test_plain_names_match_like_program_ac():
    assert system.plain("FLO DESK") == system.plain("flo-desk") == "flodesk"
    assert system.plain("Not Defteri") == "notdefteri"
    assert system.plain("İnternet Gezgini") == "internetgezgini"


async def test_opens_exact_match(settings, programs):
    res = await system.open_program("flo desk", settings)
    assert res["status"] == "ok"
    assert programs == [str(settings.programs_dir / "Flo Desk.lnk")]


async def test_exact_match_beats_partial(settings, programs):
    res = await system.open_program("Google Chrome", settings)
    assert res["status"] == "ok"
    assert programs[0].endswith("Google Chrome.lnk")


async def test_ambiguous_name_asks_which(settings, programs):
    res = await system.open_program("chrome", settings)
    assert res["status"] == "ambiguous"
    assert "Google Chrome" in res["message"] and "Chrome Remote Desktop" in res["message"]
    assert "Uninstall" not in res["message"]
    assert programs == []


async def test_builtin_apps(settings, programs):
    res = await system.open_program("not defteri", settings)
    assert res["status"] == "ok"
    assert programs == ["notepad.exe"]


async def test_unknown_program(settings, programs):
    res = await system.open_program("photoshop", settings)
    assert res["status"] == "error"
    assert programs == []


REAL_PROGRAM_FILES = system.program_files  # before conftest stubs it for each test


async def test_falls_back_to_installed_programs(settings, programs, monkeypatch):
    # e.g. AnyDesk installed by winget: no Start Menu shortcut.
    monkeypatch.setattr(system, "start_apps", lambda w: "shell:AppsFolder\\AnyDesk.App")
    res = await system.open_program("AnyDesk", settings)
    assert res["status"] == "ok" and res["message"] == "AnyDesk açıldı."
    assert programs == ["shell:AppsFolder\\AnyDesk.App"]


def test_program_files_scan(tmp_path, monkeypatch):
    app = tmp_path / "PF" / "AnyDesk"
    app.mkdir(parents=True)
    (app / "AnyDesk.exe").write_text("")
    (app / "uninstall.exe").write_text("")
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "PF"))
    assert REAL_PROGRAM_FILES("anydesk") == str(app / "AnyDesk.exe")
    assert REAL_PROGRAM_FILES("photoshop") is None


async def test_dry_run_opens_nothing(settings, programs):
    settings.dry_run = True
    res = await system.open_program("flo desk", settings)
    assert res["status"] == "ok" and res["message"].startswith("(deneme)")
    assert programs == []


# ---------- screenshot / shutdown (subprocess faked) ----------


class Recorder(list):
    """Stands in for subprocess.run: records each command, returns `result`."""

    def __init__(self):
        super().__init__()
        self.result = SimpleNamespace(returncode=0, stdout="", stderr="")

    def __call__(self, cmd, **kwargs):
        self.append(cmd)
        return self.result


@pytest.fixture
def ran(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(system.subprocess, "run", recorder)
    return recorder


async def test_screenshot_returns_path_and_url(settings, ran):
    path = r"C:\Users\Ada\Pictures\Screenshots\Ekran görüntüsü 2026-11-03 101500.png"
    ran.result.stdout = f"Ekranın görüntüsü alındı ve kaydedildi: {path}\n"
    res = await system.take_screenshot("", settings)
    assert res["status"] == "ok"
    assert res["path"] == path
    assert res["image_url"] == "/api/screenshots/Ekran görüntüsü 2026-11-03 101500.png"
    # The window name is passed as one argument, never as code.
    assert ran[0][-2:] == [str(settings.screenshot_script), ""]


async def test_screenshot_error_from_script(settings, ran):
    ran.result.returncode = 1
    ran.result.stdout = "Hata: 'paint' adında açık bir pencere bulunamadı.\n"
    res = await system.take_screenshot("paint; Remove-Item C:\\", settings)
    assert res == {"status": "error", "message": "'paint' adında açık bir pencere bulunamadı."}
    assert ran[0][-1] == "paint; Remove-Item C:\\"


async def test_shutdown_uses_30s_delay(settings, ran):
    res = await system.shutdown("restart", settings)
    assert res["status"] == "ok"
    assert ran[0][:4] == ["shutdown.exe", "/r", "/t", "30"]


async def test_shutdown_already_scheduled(settings, ran):
    ran.result.returncode = 1190
    assert (await system.shutdown("shutdown", settings))[
        "message"
    ] == "Zaten zamanlanmış bir kapatma var."


async def test_shutdown_dry_run_runs_nothing(settings, ran):
    settings.dry_run = True
    assert (await system.shutdown("shutdown", settings))["message"].startswith("(deneme)")
    assert ran == []


async def test_cancel_shutdown(settings, ran):
    assert (await system.cancel_shutdown(settings))["message"] == "Kapatma iptal edildi."
    assert ran[0] == ["shutdown.exe", "/a"]


# ---------- weather / time ----------


def weather_api(request: httpx.Request) -> httpx.Response:
    if "geocoding" in request.url.host:
        if request.url.params["name"] == "Atlantis":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "results": [
                    {"name": "İstanbul", "country": "Türkiye", "latitude": 41.0, "longitude": 29.0}
                ]
            },
        )
    return httpx.Response(
        200,
        json={
            "current": {
                "temperature_2m": 14.6,
                "apparent_temperature": 12.2,
                "weather_code": 61,
                "wind_speed_10m": 18.0,
            },
            "daily": {
                "temperature_2m_max": [17.1],
                "temperature_2m_min": [10.4],
                "precipitation_probability_max": [80],
            },
        },
    )


@pytest.fixture
def fake_weather(monkeypatch):
    monkeypatch.setattr(web, "transport", httpx.MockTransport(weather_api))


async def test_weather(fake_weather):
    res = await web.get_weather("istanbul")
    assert res["status"] == "ok"
    assert res["message"] == (
        "İstanbul: hafif yağmurlu, 15 derece (hissedilen 12). Bugün en düşük 10, en yüksek 17 derece, "
        "yağış ihtimali yüzde 80."
    )


async def test_weather_unknown_city(fake_weather):
    assert (await web.get_weather("Atlantis"))["status"] == "error"


async def test_weather_service_down(monkeypatch):
    monkeypatch.setattr(web, "transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    res = await web.get_weather("İzmir")
    assert res["status"] == "error" and "ulaşılamadı" in res["message"]


async def test_time_in_turkish():
    res = await web.get_time(dt.datetime(2026, 11, 3, 9, 5))
    assert res["message"] == "3 Kasım 2026 Salı, saat 09:05."


# ---------- Claude Code jobs ----------


async def wait_done(jobs, job_id):
    for _ in range(100):
        if jobs.jobs[job_id].state != "running":
            return jobs.jobs[job_id]
        await asyncio.sleep(0.01)
    raise AssertionError("job never finished")


async def test_claude_job_success(ctx, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"is_error": False, "result": "Masaüstünde 4 dosya var."}),
            stderr="",
        )

    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    started = await ctx.jobs.start("--help masaüstündeki dosyaları listele")
    job = await wait_done(ctx.jobs, started["job_id"])

    assert job.state == "done" and job.result == "Masaüstünde 4 dosya var."
    assert seen["input"] == "--help masaüstündeki dosyaları listele"  # task via stdin, never argv
    assert "--help" not in seen["cmd"]
    assert "Bash(rm -rf:*)" in seen["cmd"]
    assert (await ctx.jobs.status(None))["message"] == "Görev bitti. Masaüstünde 4 dosya var."
    assert audit.tail(ctx.settings.audit_log)[-1]["tool"] == "claude_job_finished"


async def test_claude_not_installed(ctx):
    started = await ctx.jobs.start("bir şey yap")
    job = await wait_done(ctx.jobs, started["job_id"])
    assert job.state == "failed" and "kurulu olmayabilir" in job.result


async def test_claude_not_logged_in(ctx, monkeypatch):
    out = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
    monkeypatch.setattr(
        claude.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=out, stderr=""),
    )
    job = await wait_done(ctx.jobs, (await ctx.jobs.start("x"))["job_id"])
    assert "/login" in job.result and "oturum açılmamış" in job.result


async def test_claude_timeout(ctx, monkeypatch):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(claude.subprocess, "run", slow)
    job = await wait_done(ctx.jobs, (await ctx.jobs.start("x"))["job_id"])
    assert job.state == "failed" and "dakikada bitmedi" in job.result


async def test_empty_task_is_rejected(ctx):
    assert (await ctx.jobs.start("  "))["status"] == "error"


def test_delegate_through_api_needs_voice_yes_then_polls(client, ctx, monkeypatch):
    out = json.dumps({"is_error": False, "result": "Tamamlandı."})
    monkeypatch.setattr(
        claude.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout=out, stderr=""),
    )

    ask = client.post(
        "/api/tool", json={"name": "delegate_to_claude", "args": {"task": "README'yi güncelle"}}
    ).json()
    assert ask["status"] == "needs_confirmation" and ctx.jobs.jobs == {}

    done = client.post(
        "/api/tool",
        json={
            "name": "confirm_action",
            "args": {"confirmation_id": ask["confirmation_id"]},
            "utterances": ["evet ver"],
        },
    ).json()
    assert done["status"] == "ok"
    job_id = done["job_id"]
    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["state"] != "running":
            break
        time.sleep(0.02)
    assert status["state"] == "done" and status["result"] == "Tamamlandı."
    assert client.get("/api/jobs/unknown").status_code == 404
