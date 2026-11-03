"""Windows commands. Program lookup is ported from JARVIS-ekler\\program-ac.ps1,
the screenshot script is JARVIS-ekler\\ekran-goruntusu.ps1, and shutdown follows
bilgisayar-kapat.ps1 (30 s delay so it can still be cancelled)."""

import asyncio
import os
import re
import subprocess
from pathlib import Path

from ..config import Settings

SHORTCUT_EXT = {".lnk", ".url", ".bat", ".cmd", ".exe"}
SHUTDOWN_DELAY = 30
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Built-in apps that have no Start Menu shortcut on Windows 11.
BUILTINS = {
    "notdefteri": "notepad.exe",
    "notepad": "notepad.exe",
    "hesapmakinesi": "calc.exe",
    "calculator": "calc.exe",
    "dosyagezgini": "explorer.exe",
    "explorer": "explorer.exe",
    "gorevyoneticisi": "taskmgr.exe",
    "paint": "mspaint.exe",
    "ayarlar": "ms-settings:",
    "komutistemi": "cmd.exe",
    "powershell": "powershell.exe",
}


def plain(s: str) -> str:
    """'FLO DESK' == 'flodesk', 'Not Defteri' == 'notdefteri' (same rule as program-ac.ps1)."""
    s = s.replace("İ", "i").replace("I", "i").lower()
    for a, b in zip("ışğüöç", "isguoc"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", s)


def shortcut_dirs(settings: Settings) -> list[Path]:
    start_menu = r"Microsoft\Windows\Start Menu\Programs"
    dirs = [settings.programs_dir]
    for env in ("APPDATA", "PROGRAMDATA"):
        if os.environ.get(env):
            dirs.append(Path(os.environ[env]) / start_menu)
    return [d for d in dirs if d.is_dir()]


def find_program(name: str, settings: Settings) -> tuple[str | None, list[str]]:
    """Returns (target to open, []) on a single match, else (None, candidate names)."""
    wanted = plain(name)
    if not wanted:
        return None, []
    for d in shortcut_dirs(settings):
        files = [p for p in d.rglob("*") if p.suffix.lower() in SHORTCUT_EXT and "uninstall" not in p.stem.lower()]
        exact = [p for p in files if plain(p.stem) == wanted]
        if len(exact) >= 1:
            return str(exact[0]), []
        partial = [p for p in files if wanted in plain(p.stem) or (len(plain(p.stem)) > 2 and plain(p.stem) in wanted)]
        if len(partial) == 1:
            return str(partial[0]), []
        if partial:
            return None, sorted({p.stem for p in partial})[:8]
    if wanted in BUILTINS:
        return BUILTINS[wanted], []
    return None, []


async def open_program(name: str, settings: Settings) -> dict:
    target, candidates = await asyncio.to_thread(find_program, name, settings)
    if candidates:
        return {"status": "ambiguous", "message": f"Birden fazla eşleşme var: {', '.join(candidates)}. Hangisi?"}
    if not target:
        return {"status": "error", "message": f"'{name}' adında bir program bulamadım."}
    label = Path(target).stem if os.path.sep in target else name
    if settings.dry_run:
        return {"status": "ok", "message": f"(deneme) {label} açılacaktı.", "target": target}
    try:
        os.startfile(target)  # type: ignore[attr-defined]  # Windows only
    except OSError as exc:
        return {"status": "error", "message": f"{label} açılamadı: {exc.strerror or exc}."}
    return {"status": "ok", "message": f"{label} açıldı.", "target": target}


def _powershell(script: Path, *args: str) -> subprocess.CompletedProcess:
    # -File passes each argument as-is; nothing the user says is parsed as code.
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=NO_WINDOW,
    )


async def take_screenshot(target: str, settings: Settings) -> dict:
    try:
        proc = await asyncio.to_thread(_powershell, settings.screenshot_script, target.strip())
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Ekran görüntüsü 30 saniyede alınamadı."}
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    last = lines[-1] if lines else (proc.stderr.strip() or "Ekran görüntüsü alınamadı.")
    match = re.search(r":\s*(\S.*\.png)$", last)
    if proc.returncode != 0 or not match or last.startswith("Hata"):
        return {"status": "error", "message": last.removeprefix("Hata: ")}
    path = Path(match.group(1))
    return {
        "status": "ok",
        "message": last.split(":")[0] + ".",
        "path": str(path),
        "image_url": f"/api/screenshots/{path.name}",
    }


def screenshot_dir() -> Path:
    from ctypes import windll, create_unicode_buffer  # noqa: PLC0415  # Windows only

    buf = create_unicode_buffer(260)
    windll.shell32.SHGetFolderPathW(None, 0x27, None, 0, buf)  # CSIDL_MYPICTURES
    return Path(buf.value) / "Screenshots"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=15, creationflags=NO_WINDOW)


async def lock_computer(settings: Settings) -> dict:
    if settings.dry_run:
        return {"status": "ok", "message": "(deneme) Ekran kilitlenecekti."}
    proc = await asyncio.to_thread(_run, ["rundll32.exe", "user32.dll,LockWorkStation"])
    if proc.returncode != 0:
        return {"status": "error", "message": "Ekran kilitlenemedi."}
    return {"status": "ok", "message": "Ekran kilitlendi."}


async def shutdown(mode: str, settings: Settings) -> dict:
    if mode not in ("shutdown", "restart"):
        return {"status": "error", "message": f"Bilinmeyen kapatma türü: {mode}."}
    what = "yeniden başlatılacak" if mode == "restart" else "kapatılacak"
    flag = "/r" if mode == "restart" else "/s"
    if settings.dry_run:
        return {"status": "ok", "message": f"(deneme) Bilgisayar {SHUTDOWN_DELAY} saniye içinde {what}."}
    cmd = ["shutdown.exe", flag, "/t", str(SHUTDOWN_DELAY), "/c", f"Mario: bilgisayar {SHUTDOWN_DELAY} saniye içinde {what}."]
    proc = await asyncio.to_thread(_run, cmd)
    if proc.returncode == 1190:
        return {"status": "error", "message": "Zaten zamanlanmış bir kapatma var."}
    if proc.returncode != 0:
        return {"status": "error", "message": f"Kapatma başlatılamadı (çıkış kodu {proc.returncode})."}
    return {"status": "ok", "message": f"Bilgisayar {SHUTDOWN_DELAY} saniye içinde {what}. 'İptal et' diyebilirsin."}


async def cancel_shutdown(settings: Settings) -> dict:
    if settings.dry_run:
        return {"status": "ok", "message": "(deneme) Kapatma iptal edilecekti."}
    proc = await asyncio.to_thread(_run, ["shutdown.exe", "/a"])
    if proc.returncode == 0:
        return {"status": "ok", "message": "Kapatma iptal edildi."}
    return {"status": "ok", "message": "İptal edilecek bir kapatma yoktu."}
