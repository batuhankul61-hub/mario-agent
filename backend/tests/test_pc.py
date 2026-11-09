"""PC settings with every Windows helper faked: no test changes this machine."""

import pytest
from app import audit
from app.tools import pc


@pytest.fixture
def audio(monkeypatch):
    state = {"device": "Kulaklıklar (TUF H1)", "level": 40, "muted": False, "set": []}
    monkeypatch.setattr(
        pc, "_audio_sync", lambda: {k: state[k] for k in ("device", "level", "muted")}
    )
    monkeypatch.setattr(
        pc, "_set_audio_sync", lambda level, mute: state["set"].append((level, mute))
    )
    return state


async def test_volume_level_is_clamped(settings, audio):
    res = await pc.set_volume({"level": 140}, settings)
    assert res["message"] == "Ses seviyesi yüzde 100."
    assert audio["set"] == [(100, None)]


async def test_volume_up_and_down_step_by_ten(settings, audio):
    await pc.set_volume({"change": "up"}, settings)
    await pc.set_volume({"change": "down"}, settings)
    assert audio["set"] == [(50, None), (30, None)]


async def test_setting_a_level_while_muted_unmutes(settings, audio):
    audio["muted"] = True
    await pc.set_volume({"level": 30}, settings)
    assert audio["set"] == [(30, False)]


async def test_mute(settings, audio):
    assert (await pc.set_volume({"mute": True}, settings))["message"] == "Ses kapatıldı."
    assert audio["set"] == [(None, True)]


async def test_volume_needs_something_to_do(settings, audio):
    assert (await pc.set_volume({}, settings))["status"] == "error"


async def test_volume_dry_run(settings, audio):
    settings.dry_run = True
    assert (await pc.set_volume({"level": 10}, settings))["message"].startswith("(deneme)")
    assert audio["set"] == []


@pytest.fixture
def mic(monkeypatch):
    state = {"device": "Mikrofon (TUF H1)", "muted": False, "set": []}
    monkeypatch.setattr(pc, "_mic_sync", lambda: {k: state[k] for k in ("device", "muted")})
    monkeypatch.setattr(pc, "_set_mic_sync", state["set"].append)
    return state


async def test_mute_microphone(settings, mic):
    res = await pc.set_microphone({"mute": True}, settings)
    assert res["status"] == "ok" and res["message"].startswith("Mikrofon kapatıldı")
    assert mic["set"] == [True]


async def test_microphone_already_in_wanted_state(settings, mic):
    res = await pc.set_microphone({"mute": False}, settings)
    assert res["message"] == "Mikrofon zaten açık (Mikrofon (TUF H1))."
    assert mic["set"] == []


async def test_no_microphone(settings, monkeypatch):
    monkeypatch.setattr(pc, "_mic_sync", lambda: None)
    assert (await pc.set_microphone({"mute": True}, settings))["status"] == "error"


def test_mic_off_needs_confirmation_but_mic_on_does_not(client, mic):
    mic["muted"] = True
    on = client.post("/api/tool", json={"name": "set_microphone", "args": {"mute": False}}).json()
    off = client.post("/api/tool", json={"name": "set_microphone", "args": {"mute": True}}).json()
    assert on["status"] == "ok" and mic["set"] == [False]
    assert off["status"] == "needs_confirmation" and "duyamam" in off["question"]


OUTPUTS = {
    "id-headset": "Kulaklıklar (2- TUF GAMING H1 Wireless)",
    "id-legion": "2 - Legion 25-10 (AMD High Definition Audio Device)",
    "id-asus": "1 - VG259QR (AMD High Definition Audio Device)",
}


@pytest.fixture
def outputs(monkeypatch):
    chosen = []
    monkeypatch.setattr(pc, "_outputs_sync", lambda: OUTPUTS)
    monkeypatch.setattr(pc, "_set_output_sync", chosen.append)
    return chosen


async def test_switch_output_by_spoken_name(settings, outputs):
    res = await pc.set_audio_output({"device": "kulaklık"}, settings)
    assert outputs == ["id-headset"]
    assert "TUF GAMING" in res["message"]


async def test_ambiguous_output_lists_choices(settings, outputs):
    res = await pc.set_audio_output({"device": "AMD"}, settings)
    assert (
        res["status"] == "ambiguous" and "Legion" in res["message"] and "VG259QR" in res["message"]
    )
    assert outputs == []


@pytest.fixture
def screens(monkeypatch):
    calls = []
    monkeypatch.setattr(pc, "_brightness_sync", lambda: [70, 60])
    monkeypatch.setattr(pc, "_set_brightness_sync", lambda level: calls.append(level) or 2)
    return calls


async def test_brightness_up_from_brightest_screen(settings, screens):
    assert (await pc.set_brightness({"change": "up"}, settings))["message"] == "Parlaklık yüzde 80."
    assert screens == [80]


async def test_brightness_without_ddc(settings, monkeypatch):
    monkeypatch.setattr(pc, "_brightness_sync", lambda: [50])
    monkeypatch.setattr(pc, "_set_brightness_sync", lambda level: 0)
    assert (await pc.set_brightness({"level": 20}, settings))["status"] == "error"


@pytest.fixture
def registry(monkeypatch):
    writes = []
    monkeypatch.setattr(
        pc,
        "_write_dwords",
        lambda path, values, broadcast: writes.append((path, values, broadcast)),
    )
    return writes


async def test_dark_theme(settings, registry):
    assert (await pc.set_theme({"mode": "dark"}, settings))["message"] == "Koyu temaya geçildi."
    assert registry == [
        (pc.PERSONALIZE, {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 0}, "ImmersiveColorSet")
    ]


async def test_bad_theme(settings, registry):
    assert (await pc.set_theme({"mode": "blue"}, settings))["status"] == "error"
    assert registry == []


async def test_do_not_disturb_turns_toasts_off(settings, registry):
    await pc.set_do_not_disturb({"on": True}, settings)
    assert registry[0][1] == {"NOC_GLOBAL_SETTING_TOASTS_ENABLED": 0}


async def test_open_settings_only_known_pages(settings, monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", opened.append, raising=False)
    assert (await pc.open_settings({"page": "nightlight"}, settings))[
        "message"
    ] == "Gece ışığı ayarları açıldı."
    assert (await pc.open_settings({"page": "file:///C:/Windows"}, settings))["status"] == "error"
    assert opened == ["ms-settings:nightlight"]


async def test_radio_missing(settings, monkeypatch):
    monkeypatch.setattr(pc, "_set_radio_sync", lambda kind, on: "missing")
    assert (await pc.set_radio({"radio": "wifi", "on": True}, settings))[
        "message"
    ] == "Bu bilgisayarda Wi-Fi yok."


async def test_bluetooth_off(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(pc, "_set_radio_sync", lambda kind, on: calls.append((kind, on)) or "ok")
    assert (await pc.set_radio({"radio": "bluetooth", "on": False}, settings))[
        "message"
    ] == "Bluetooth kapatıldı."
    assert calls == [("bluetooth", False)]


def test_wifi_off_needs_confirmation_but_wifi_on_does_not(client, monkeypatch, settings):
    monkeypatch.setattr(pc, "_set_radio_sync", lambda kind, on: "ok")
    on = client.post(
        "/api/tool", json={"name": "set_radio", "args": {"radio": "wifi", "on": True}}
    ).json()
    off = client.post(
        "/api/tool", json={"name": "set_radio", "args": {"radio": "wifi", "on": False}}
    ).json()
    assert on["status"] == "ok"
    assert off["status"] == "needs_confirmation" and "bağlantı kopabilir" in off["question"]


PLANS = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "Dengeli",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "Yüksek performans",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "Güç tasarrufu",
    "c8c0d4bb-2e86-4c31-96ee-1431350fbd45": "Nihai Performans",
}


@pytest.fixture
def plans(monkeypatch):
    chosen = []
    monkeypatch.setattr(pc, "_plans_sync", lambda: (PLANS, "c8c0d4bb-2e86-4c31-96ee-1431350fbd45"))
    monkeypatch.setattr(pc, "_set_plan_sync", lambda guid: chosen.append(guid) or 0)
    return chosen


@pytest.mark.parametrize(
    ("spoken", "guid"),
    [
        ("dengeli", "381b4222"),
        ("balanced", "381b4222"),
        ("güç tasarrufu", "a1841308"),
        ("nihai", "c8c0d4bb"),
    ],
)
async def test_power_plan_by_name_or_alias(settings, plans, spoken, guid):
    assert (await pc.set_power_plan({"plan": spoken}, settings))["status"] == "ok"
    assert plans[0].startswith(guid)


async def test_power_plan_ambiguous(settings, plans):
    res = await pc.set_power_plan({"plan": "performans"}, settings)
    assert res["status"] == "ambiguous"
    assert plans == []


def test_plans_parsed_from_powercfg(monkeypatch):
    out = (
        "Existing Power Schemes (* Active)\n-----\n"
        "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Dengeli)\n"
        "Power Scheme GUID: c8c0d4bb-2e86-4c31-96ee-1431350fbd45  (Nihai Performans) *\n"
    )
    monkeypatch.setattr(pc, "_run", lambda cmd: type("P", (), {"stdout": out, "returncode": 0})())
    plans, active = pc._plans_sync()
    assert plans["381b4222-f694-41f0-9685-ff5bb260df2e"] == "Dengeli"
    assert active == "c8c0d4bb-2e86-4c31-96ee-1431350fbd45"


WINDOWS = [
    {"pid": 10, "process": "chrome", "title": "Gmail - Google Chrome"},
    {"pid": 11, "process": "chrome", "title": "YouTube - Google Chrome"},
    {"pid": 20, "process": "explorer", "title": "İndirilenler"},
    {"pid": 30, "process": "python", "title": "Mario backend"},
    {"pid": 40, "process": "Discord", "title": "Discord"},
]


@pytest.fixture
def windows(monkeypatch):
    closed = []
    monkeypatch.setattr(pc, "_windows_sync", lambda: WINDOWS)
    monkeypatch.setattr(pc, "_close_sync", lambda pid: closed.append(pid) or 0)
    return closed


async def test_list_programs_hides_system_processes(settings, windows):
    res = await pc.list_programs({}, settings)
    assert res["message"] == "Açık programlar: chrome, Discord."


async def test_close_program_by_window_title(settings, windows):
    res = await pc.close_program({"name": "youtube"}, settings)
    assert res["status"] == "ok"
    assert windows == [11]


@pytest.mark.parametrize("name", ["explorer", "python", "İndirilenler", ""])
async def test_never_closes_windows_or_mario_itself(settings, windows, name):
    assert (await pc.close_program({"name": name}, settings))["status"] == "error"
    assert windows == []


def test_close_program_waits_for_voice_yes(client, windows, settings):
    ask = client.post(
        "/api/tool", json={"name": "close_program", "args": {"name": "discord"}}
    ).json()
    assert ask["status"] == "needs_confirmation" and windows == []
    done = client.post(
        "/api/tool",
        json={
            "name": "confirm_action",
            "args": {"confirmation_id": ask["confirmation_id"]},
            "utterances": ["evet kapat"],
        },
    ).json()
    assert done["status"] == "ok" and windows == [40]
    assert audit.tail(settings.audit_log)[-1]["confirmed_by"] == "voice: evet kapat"


async def test_sleep_dry_run(settings, monkeypatch):
    slept = []
    monkeypatch.setattr(pc, "_sleep_sync", lambda: slept.append(1))
    settings.dry_run = True
    assert (await pc.sleep_computer({}, settings))["message"].startswith("(deneme)")
    assert slept == []


async def test_status_survives_a_failing_part(settings, monkeypatch, audio, mic):
    def broken():
        raise OSError("no DDC")

    monkeypatch.setattr(pc, "_brightness_sync", broken)
    monkeypatch.setattr(pc, "_theme_sync", lambda: "dark")
    monkeypatch.setattr(pc, "_notifications_sync", lambda: False)
    monkeypatch.setattr(pc, "_radios_sync", lambda: {"bluetooth": "on"})
    monkeypatch.setattr(
        pc,
        "_network_sync",
        lambda: {"wifi": None, "signal": None, "wired": [{"name": "Ethernet", "speed_mbps": 1000}]},
    )
    monkeypatch.setattr(pc, "_plans_sync", lambda: (PLANS, "381b4222-f694-41f0-9685-ff5bb260df2e"))
    monkeypatch.setattr(pc, "_system_sync", lambda: {"cpu": 12, "ram": 70, "battery": None})

    res = await pc.get_pc_status({}, settings)

    assert res["brightness"] is None
    assert res["message"] == (
        "Ses yüzde 40, çıkış: Kulaklıklar (TUF H1). Mikrofon açık. Koyu tema. Bildirimler susturulmuş. Bluetooth açık. "
        "Kablolu: Ethernet (1 Gbit/s). Güç planı Dengeli. İşlemci yüzde 12, bellek yüzde 70."
    )
