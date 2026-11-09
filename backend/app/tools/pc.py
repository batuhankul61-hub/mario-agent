"""PC settings: sound, screen, appearance, network, power and running programs.

Each tool is an async function with the logic (matching names, clamping,
wording) and calls small `_sync` helpers that touch Windows (COM, WinRT,
registry, powercfg). Tests replace the helpers, so nothing here changes the
machine during a test run.
"""

import asyncio
import ctypes
import re
import subprocess
import warnings
from contextlib import contextmanager

from ..config import Settings
from .system import NO_WINDOW, plain

VOLUME_STEP = 10

# Settings pages that open_settings may open (no free-form URIs).
SETTINGS_PAGES = {
    "nightlight": ("ms-settings:nightlight", "Gece ışığı"),
    "display": ("ms-settings:display", "Ekran"),
    "sound": ("ms-settings:sound", "Ses"),
    "bluetooth": ("ms-settings:bluetooth", "Bluetooth"),
    "network": ("ms-settings:network-status", "Ağ"),
    "notifications": ("ms-settings:notifications", "Bildirimler"),
    "power": ("ms-settings:powersleep", "Güç ve uyku"),
    "personalization": ("ms-settings:personalization-colors", "Renkler"),
}

# Built-in power plans; names are localized ("Dengeli"), GUIDs are not.
PLAN_ALIASES = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": ["balanced", "dengeli", "normal"],
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": ["highperformance", "yuksekperformans"],
    "a1841308-3541-4fab-bc81-f71556f20b4a": ["powersaver", "guctasarrufu", "tasarruf"],
    "e9a42b02-d5df-448d-aa00-03f14749eb61": ["ultimateperformance", "nihaiperformans", "nihai"],
}

# Never closed by close_program: Windows itself, and Mario's own processes.
PROTECTED = {
    "explorer",
    "winlogon",
    "csrss",
    "lsass",
    "services",
    "smss",
    "svchost",
    "dwm",
    "wininit",
    "fontdrvhost",
    "sihost",
    "taskhostw",
    "python",
    "pythonw",
    "uvicorn",
    "tailscaled",
    "tailscaleipn",
}

ok = lambda message, **extra: {"status": "ok", "message": message, **extra}  # noqa: E731
err = lambda message: {"status": "error", "message": message}  # noqa: E731


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # Console tools like powercfg print in the OEM code page (cp857 for Turkish).
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        timeout=15,
        creationflags=NO_WINDOW,
    )


@contextmanager
def _com():
    import comtypes  # noqa: PLC0415  # Windows only

    comtypes.CoInitialize()
    try:
        yield
    finally:
        comtypes.CoUninitialize()


def pick(name: str, options: dict[str, str]) -> tuple[str | None, list[str]]:
    """Fuzzy match a spoken name against {key: label}. Returns (key, []) or (None, candidates)."""
    wanted = plain(name)
    if not wanted:
        return None, list(options.values())
    exact = [k for k, label in options.items() if plain(label) == wanted]
    if exact:
        return exact[0], []
    partial = [
        k for k, label in options.items() if wanted in plain(label) or plain(label) in wanted
    ]
    if len(partial) == 1:
        return partial[0], []
    return None, [options[k] for k in partial]


# ---------- sound ----------


def _audio_sync() -> dict:
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    with _com():
        speakers = AudioUtilities.GetSpeakers()
        vol = speakers.EndpointVolume
        return {
            "device": speakers.FriendlyName,
            "level": round(vol.GetMasterVolumeLevelScalar() * 100),
            "muted": bool(vol.GetMute()),
        }


def _set_audio_sync(level: int | None, mute: bool | None) -> None:
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    with _com():
        vol = AudioUtilities.GetSpeakers().EndpointVolume
        if level is not None:
            vol.SetMasterVolumeLevelScalar(level / 100, None)
        if mute is not None:
            vol.SetMute(int(mute), None)


def _outputs_sync() -> dict[str, str]:
    from pycaw.constants import DEVICE_STATE, EDataFlow  # noqa: PLC0415
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    with _com(), warnings.catch_warnings():
        warnings.simplefilter("ignore")  # pycaw warns about unreadable optional properties
        found = AudioUtilities.GetDeviceEnumerator().EnumAudioEndpoints(
            EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value
        )
        devices = [AudioUtilities.CreateDevice(found.Item(i)) for i in range(found.GetCount())]
        return {d.id: d.FriendlyName for d in devices}


def _set_output_sync(device_id: str) -> None:
    from pycaw.constants import ERole  # noqa: PLC0415
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    with _com():
        AudioUtilities.SetDefaultDevice(
            device_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications]
        )


async def set_volume(args: dict, settings: Settings) -> dict:
    level, change, mute = args.get("level"), args.get("change"), args.get("mute")
    if level is None and change is None and mute is None:
        return err("Ses için seviye, artır/azalt ya da sessize al/aç belirtilmeli.")
    current = await asyncio.to_thread(_audio_sync)
    if change in ("up", "down"):
        level = current["level"] + (VOLUME_STEP if change == "up" else -VOLUME_STEP)
    if level is not None:
        level = max(0, min(100, int(level)))
        if mute is None and level > 0 and current["muted"]:
            mute = False  # "sesi 40 yap" while muted means: unmute too
    if settings.dry_run:
        return ok(f"(deneme) Ses {level if level is not None else current['level']} olacaktı.")
    await asyncio.to_thread(_set_audio_sync, level, None if mute is None else bool(mute))
    if mute:
        return ok("Ses kapatıldı.")
    return ok(f"Ses seviyesi yüzde {level if level is not None else current['level']}.")


def _mic_sync() -> dict | None:
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    with _com():
        found = AudioUtilities.GetMicrophone()
        if not found:
            return None
        mic = AudioUtilities.CreateDevice(found)
        return {"device": mic.FriendlyName, "muted": bool(mic.EndpointVolume.GetMute())}


def _set_mic_sync(mute: bool) -> None:
    from pycaw.pycaw import AudioUtilities  # noqa: PLC0415

    with _com():
        AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone()).EndpointVolume.SetMute(
            int(mute), None
        )


async def set_microphone(args: dict, settings: Settings) -> dict:
    """Mutes the default recording device for every app (Discord, Zoom, Chrome...)."""
    mute = bool(args.get("mute", True))
    mic = await asyncio.to_thread(_mic_sync)
    if mic is None:
        return err("Bu bilgisayarda varsayılan bir mikrofon yok.")
    if mic["muted"] == mute:
        return ok(f"Mikrofon zaten {'kapalı' if mute else 'açık'} ({mic['device']}).")
    if settings.dry_run:
        return ok(f"(deneme) Mikrofon {'kapatılacaktı' if mute else 'açılacaktı'}.")
    await asyncio.to_thread(_set_mic_sync, mute)
    if mute:
        return ok(
            f"Mikrofon kapatıldı ({mic['device']}). Bu bilgisayardan konuşuyorsan seni artık duyamam; "
            "açmak için 'mikrofonu aç' yazabilirsin."
        )
    return ok(f"Mikrofon açıldı ({mic['device']}).")


async def set_audio_output(args: dict, settings: Settings) -> dict:
    outputs = await asyncio.to_thread(_outputs_sync)
    key, candidates = pick(args.get("device", ""), outputs)
    if not key:
        names = ", ".join(candidates or outputs.values())
        return {"status": "ambiguous", "message": f"Hangi cihaz? Seçenekler: {names}."}
    if settings.dry_run:
        return ok(f"(deneme) Ses {outputs[key]} cihazından çıkacaktı.")
    await asyncio.to_thread(_set_output_sync, key)
    return ok(f"Ses artık {outputs[key]} cihazından çıkıyor.")


# ---------- screen and appearance ----------


def _brightness_sync() -> list[int]:
    import screen_brightness_control as sbc  # noqa: PLC0415

    return sbc.get_brightness()


def _set_brightness_sync(level: int) -> int:
    """Returns how many displays accepted the change (DDC/CI can be off on some)."""
    import screen_brightness_control as sbc  # noqa: PLC0415

    done = 0
    for display in sbc.list_monitors():
        try:
            sbc.set_brightness(level, display=display)
            done += 1
        except Exception:  # noqa: BLE001  # one monitor without DDC/CI shouldn't stop the rest
            pass
    return done


async def set_brightness(args: dict, settings: Settings) -> dict:
    current = await asyncio.to_thread(_brightness_sync)
    level, change = args.get("level"), args.get("change")
    if change in ("up", "down"):
        base = max(current) if current else 50
        level = base + (VOLUME_STEP if change == "up" else -VOLUME_STEP)
    if level is None:
        return err("Parlaklık için seviye ya da artır/azalt belirtilmeli.")
    level = max(0, min(100, int(level)))
    if settings.dry_run:
        return ok(f"(deneme) Parlaklık yüzde {level} olacaktı.")
    done = await asyncio.to_thread(_set_brightness_sync, level)
    if not done:
        return err("Parlaklık değiştirilemedi; monitörde DDC/CI kapalı olabilir.")
    return ok(
        f"Parlaklık yüzde {level}."
        + ("" if done == len(current) else f" ({done} ekranda değişti.)")
    )


PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
NOTIFICATIONS = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"


def _read_dword(path: str, name: str, default: int) -> int:
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return int(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return default


def _write_dwords(path: str, values: dict[str, int], broadcast: str) -> None:
    import winreg  # noqa: PLC0415

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    # Tell open windows (taskbar, apps) to re-read the setting.
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, broadcast, 0x0002, 3000, ctypes.byref(result)
    )


def _theme_sync() -> str:
    return "light" if _read_dword(PERSONALIZE, "AppsUseLightTheme", 1) else "dark"


async def set_theme(args: dict, settings: Settings) -> dict:
    mode = args.get("mode")
    if mode not in ("dark", "light"):
        return err("Tema 'dark' ya da 'light' olmalı.")
    label = "Koyu" if mode == "dark" else "Açık"
    if settings.dry_run:
        return ok(f"(deneme) {label} temaya geçilecekti.")
    value = int(mode == "light")
    await asyncio.to_thread(
        _write_dwords,
        PERSONALIZE,
        {"AppsUseLightTheme": value, "SystemUsesLightTheme": value},
        "ImmersiveColorSet",
    )
    return ok(f"{label} temaya geçildi.")


def _notifications_sync() -> bool:
    return bool(_read_dword(NOTIFICATIONS, "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 1))


async def set_do_not_disturb(args: dict, settings: Settings) -> dict:
    """Windows has no public Do Not Disturb API. This flips the "Notifications"
    switch in Settings (no banners or sounds), which is the documented setting
    closest to it."""
    on = bool(args.get("on", True))
    if settings.dry_run:
        return ok(f"(deneme) Rahatsız etme {'açılacaktı' if on else 'kapatılacaktı'}.")
    await asyncio.to_thread(
        _write_dwords,
        NOTIFICATIONS,
        {"NOC_GLOBAL_SETTING_TOASTS_ENABLED": int(not on)},
        "Notifications",
    )
    return ok("Bildirimler susturuldu." if on else "Bildirimler yeniden açık.")


async def open_settings(args: dict, settings: Settings) -> dict:
    page = SETTINGS_PAGES.get(args.get("page", ""))
    if page is None:
        return err(f"Bilinmeyen ayar sayfası. Seçenekler: {', '.join(SETTINGS_PAGES)}.")
    uri, label = page
    if settings.dry_run:
        return ok(f"(deneme) {label} ayarları açılacaktı.")
    import os  # noqa: PLC0415

    os.startfile(uri)  # type: ignore[attr-defined]
    return ok(f"{label} ayarları açıldı.")


# ---------- network ----------


def _radios_sync() -> dict[str, str]:
    from winrt.windows.devices.radios import Radio, RadioKind, RadioState  # noqa: PLC0415

    async def read():
        radios = await Radio.get_radios_async()
        kinds = {RadioKind.WI_FI: "wifi", RadioKind.BLUETOOTH: "bluetooth"}
        return {
            kinds[r.kind]: ("on" if r.state == RadioState.ON else "off")
            for r in radios
            if r.kind in kinds
        }

    return asyncio.run(read())


def _set_radio_sync(kind: str, on: bool) -> str:
    from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState  # noqa: PLC0415

    async def change():
        if await Radio.request_access_async() != RadioAccessStatus.ALLOWED:
            return "denied"
        target = RadioKind.WI_FI if kind == "wifi" else RadioKind.BLUETOOTH
        radios = [r for r in await Radio.get_radios_async() if r.kind == target]
        if not radios:
            return "missing"
        for r in radios:
            await r.set_state_async(RadioState.ON if on else RadioState.OFF)
        return "ok"

    return asyncio.run(change())


async def set_radio(args: dict, settings: Settings) -> dict:
    kind, on = args.get("radio"), bool(args.get("on"))
    if kind not in ("wifi", "bluetooth"):
        return err("Radyo 'wifi' ya da 'bluetooth' olmalı.")
    label = "Wi-Fi" if kind == "wifi" else "Bluetooth"
    if settings.dry_run:
        return ok(f"(deneme) {label} {'açılacaktı' if on else 'kapatılacaktı'}.")
    result = await asyncio.to_thread(_set_radio_sync, kind, on)
    if result == "missing":
        return err(f"Bu bilgisayarda {label} yok.")
    if result == "denied":
        return err(f"Windows {label} ayarını değiştirmeye izin vermedi.")
    return ok(f"{label} {'açıldı' if on else 'kapatıldı'}.")


def _network_sync() -> dict:
    import psutil  # noqa: PLC0415

    wifi = _run(["netsh", "wlan", "show", "interfaces"]).stdout
    ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", wifi, re.M)
    signal = re.search(r"(\d+)%", wifi)
    wired = [
        {"name": name, "speed_mbps": st.speed}
        for name, st in psutil.net_if_stats().items()
        if st.isup
        and st.speed
        and not re.search(r"loopback|vethernet|virtual|vmware|tailscale|bluetooth", name, re.I)
    ]
    return {
        "wifi": ssid.group(1).strip() if ssid else None,
        "signal": int(signal.group(1)) if signal and ssid else None,
        "wired": wired,
    }


def describe_network(net: dict) -> str:
    parts = []
    if net["wifi"]:
        parts.append(
            f"Wi-Fi: {net['wifi']}" + (f", sinyal yüzde {net['signal']}" if net["signal"] else "")
        )
    for w in net["wired"]:
        speed = (
            f"{w['speed_mbps'] // 1000} Gbit/s"
            if w["speed_mbps"] >= 1000
            else f"{w['speed_mbps']} Mbit/s"
        )
        parts.append(f"kablolu: {w['name']} ({speed})")
    return "; ".join(parts) or "Bağlı bir ağ görünmüyor."


# ---------- power ----------


def _plans_sync() -> tuple[dict[str, str], str | None]:
    out = _run(["powercfg", "/list"]).stdout
    plans, active = {}, None
    for guid, name, star in re.findall(r"([0-9a-f-]{36})\s+\((.+?)\)\s*(\*?)", out):
        plans[guid] = name
        if star:
            active = guid
    return plans, active


def _set_plan_sync(guid: str) -> int:
    return _run(["powercfg", "/setactive", guid]).returncode


async def set_power_plan(args: dict, settings: Settings) -> dict:
    plans, _ = await asyncio.to_thread(_plans_sync)
    wanted = plain(args.get("plan", ""))
    key = next((g for g, names in PLAN_ALIASES.items() if g in plans and wanted in names), None)
    candidates: list[str] = []
    if not key:
        key, candidates = pick(args.get("plan", ""), plans)
    if not key:
        return {
            "status": "ambiguous",
            "message": f"Hangi güç planı? Seçenekler: {', '.join(candidates or plans.values())}.",
        }
    if settings.dry_run:
        return ok(f"(deneme) {plans[key]} planına geçilecekti.")
    if await asyncio.to_thread(_set_plan_sync, key) != 0:
        return err("Güç planı değiştirilemedi.")
    return ok(f"Güç planı: {plans[key]}.")


def _sleep_sync() -> bool:
    # bHibernate=False, bForce=False, bWakeupEventsDisabled=False: plain sleep.
    return bool(ctypes.windll.powrprof.SetSuspendState(False, False, False))


async def sleep_computer(args: dict, settings: Settings) -> dict:
    if settings.dry_run:
        return ok("(deneme) Bilgisayar uyku moduna geçecekti.")
    loop = asyncio.get_running_loop()
    # Answer first, then sleep: the reply has to reach the browser.
    loop.call_later(2, lambda: asyncio.ensure_future(asyncio.to_thread(_sleep_sync)))
    return ok("Bilgisayar iki saniye içinde uyku moduna geçiyor.")


# ---------- status and programs ----------


def _system_sync() -> dict:
    import psutil  # noqa: PLC0415

    battery = psutil.sensors_battery()
    return {
        "cpu": round(psutil.cpu_percent(interval=0.3)),
        "ram": round(psutil.virtual_memory().percent),
        "battery": None
        if battery is None
        else {"percent": round(battery.percent), "plugged": battery.power_plugged},
    }


async def get_pc_status(args: dict, settings: Settings) -> dict:
    """One read-only summary, so "durum ne?" needs a single call. Each part may fail on its own."""
    parts = {
        "audio": _audio_sync,
        "microphone": _mic_sync,
        "brightness": _brightness_sync,
        "theme": _theme_sync,
        "notifications": _notifications_sync,
        "radios": _radios_sync,
        "network": _network_sync,
        "power_plans": _plans_sync,
        "system": _system_sync,
    }
    results = await asyncio.gather(
        *(asyncio.to_thread(f) for f in parts.values()), return_exceptions=True
    )
    data = {k: (None if isinstance(v, BaseException) else v) for k, v in zip(parts, results)}

    said = []
    if a := data["audio"]:
        said.append(
            f"Ses yüzde {a['level']}{' (kapalı)' if a['muted'] else ''}, çıkış: {a['device']}"
        )
    if m := data["microphone"]:
        said.append(f"mikrofon {'kapalı' if m['muted'] else 'açık'}")
    if b := data["brightness"]:
        said.append(f"parlaklık yüzde {max(b)}")
    if data["theme"]:
        said.append("koyu tema" if data["theme"] == "dark" else "açık tema")
    if data["notifications"] is not None:
        said.append("bildirimler açık" if data["notifications"] else "bildirimler susturulmuş")
    if r := data["radios"]:
        said += [
            f"{'Wi-Fi' if k == 'wifi' else 'Bluetooth'} {'açık' if v == 'on' else 'kapalı'}"
            for k, v in r.items()
        ]
    if n := data["network"]:
        said.append(describe_network(n))
    if p := data["power_plans"]:
        plans, active = p
        if active:
            said.append(f"güç planı {plans[active]}")
    if s := data["system"]:
        said.append(f"işlemci yüzde {s['cpu']}, bellek yüzde {s['ram']}")
        if s["battery"]:
            said.append(
                f"pil yüzde {s['battery']['percent']}{', şarjda' if s['battery']['plugged'] else ''}"
            )
    data["power_plans"] = data["power_plans"][0] if data["power_plans"] else None
    return ok(". ".join(capitalize(x) for x in said) + ".", **data)


def capitalize(text: str) -> str:
    # str.upper() turns "i" into "I"; Turkish needs "İ" ("İşlemci").
    return ("İ" if text[0] == "i" else text[0].upper()) + text[1:]


def _windows_sync() -> list[dict]:
    """Visible top-level windows with a title, and the process behind each."""
    from ctypes import wintypes  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    user32 = ctypes.windll.user32
    found: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        cloaked = wintypes.DWORD()  # hidden UWP hosts such as TextInputHost are "cloaked"
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        if user32.IsWindowVisible(hwnd) and not cloaked.value and user32.GetWindowTextLengthW(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                name = psutil.Process(pid.value).name().removesuffix(".exe")
            except psutil.Error:
                return True
            found.append({"pid": pid.value, "process": name, "title": buf.value, "hwnd": hwnd})
        return True

    user32.EnumWindows(visit, 0)
    return found


def _close_sync(pid: int) -> int:
    # No /F: the app gets a normal close request and can still ask to save.
    return _run(["taskkill", "/PID", str(pid)]).returncode


def matching_windows(name: str, windows: list[dict]) -> list[dict]:
    wanted = plain(name)
    if not wanted:
        return []
    return [
        w
        for w in windows
        if plain(w["process"]) not in PROTECTED
        and (wanted in plain(w["process"]) or wanted in plain(w["title"]))
    ]


async def list_programs(args: dict, settings: Settings) -> dict:
    windows = [
        w for w in await asyncio.to_thread(_windows_sync) if plain(w["process"]) not in PROTECTED
    ]
    names = sorted({w["process"] for w in windows}, key=str.lower)
    return ok(
        f"Açık programlar: {', '.join(names)}." if names else "Açık bir program penceresi yok.",
        windows=windows,
    )


async def close_program(args: dict, settings: Settings) -> dict:
    name = args.get("name", "")
    matches = matching_windows(name, await asyncio.to_thread(_windows_sync))
    if not matches:
        return err(f"'{name}' adında açık bir program bulamadım (sistem programları kapatılmaz).")
    pids = sorted({w["pid"] for w in matches})
    label = ", ".join(sorted({w["process"] for w in matches}))
    if settings.dry_run:
        return ok(f"(deneme) {label} kapatılacaktı.")
    failed = [pid for pid in pids if await asyncio.to_thread(_close_sync, pid) != 0]
    if len(failed) == len(pids):
        return err(f"{label} kapatılamadı.")
    return ok(f"{label} için kapatma isteği gönderildi. Kaydedilmemiş iş varsa program sorabilir.")
