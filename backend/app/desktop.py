"""Mario.exe: the desktop launcher (packaging/mario_exe.py is its entry point).

Double-click → first run asks for the Gemini key, then the server starts in this
process, Mario opens in Chrome/Edge as an app window and a tray icon stays next
to the clock (open, settings, start with Windows, quit). A second double-click
only opens the window again.

Everything here also works from source (`uv run python -m app.desktop`).
"""

import contextlib
import logging
import logging.config
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from .config import ENV_FILE, FROZEN, PYTHON, RESOURCE_DIR, STATE_DIR, USER_DIR

# MARIO_PORT: a second copy beside a running one (tests, a source checkout).
PORT = int(os.environ.get("MARIO_PORT") or 8100)
URL = f"http://localhost:{PORT}"
LOG_FILE = STATE_DIR / "data" / "server.log"
KEY_URL = "https://aistudio.google.com/apikey"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

log = logging.getLogger("mario.desktop")


# --- first run -----------------------------------------------------------------


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
            if m:
                values[m[1]] = m[2].strip()
    return values


def write_env(values: dict[str, str], path: Path = ENV_FILE, template: Path | None = None) -> None:
    """Sets KEY=value lines, keeping everything else (comments, other keys).

    A new file starts from .env.example so the optional settings are listed in it.
    """
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    elif template and template.is_file():
        text = template.read_text(encoding="utf-8")
    else:
        text = ""
    lines = text.splitlines()
    for key, value in values.items():
        new = f"{key}={value}"
        for i, line in enumerate(lines):
            # Also replaces the commented-out example (`# USER_NAME=Ada`).
            if re.match(rf"\s*(#\s*)?{key}\s*=", line):
                lines[i] = new
                break
        else:
            lines.append(new)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_key(key: str) -> bool | None:
    """True/False when Google answers, None when it can't be reached (don't block on that)."""
    import httpx  # noqa: PLC0415

    try:
        res = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            params={"pageSize": 1},
            timeout=10,
        )
    except httpx.HTTPError:
        return None
    return res.status_code == 200


def ask_for_key() -> bool:
    """The first-run window. Returns False when the user closes it."""
    import tkinter as tk  # noqa: PLC0415
    from tkinter import messagebox, ttk  # noqa: PLC0415

    with contextlib.suppress(AttributeError, OSError):
        import ctypes  # noqa: PLC0415

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
    root = tk.Tk()
    root.title("Mario kurulumu")
    root.resizable(False, False)
    with contextlib.suppress(tk.TclError):
        root.iconbitmap(str(RESOURCE_DIR / "packaging" / "mario.ico"))
    frame = ttk.Frame(root, padding=20)
    frame.grid()
    ttk.Label(frame, text="Mario'ya hoş geldin!", font=("Segoe UI", 14, "bold")).grid(
        sticky="w", pady=(0, 8)
    )
    ttk.Label(
        frame,
        text=(
            "Mario, Google'ın Gemini modeliyle konuşur. Bunun için ücretsiz bir\n"
            "Gemini API anahtarı gerekiyor. Google hesabınla giriş yapıp\n"
            '"Create API key" düğmesine bas, anahtarı kopyala ve buraya yapıştır.'
        ),
        justify="left",
    ).grid(sticky="w")
    ttk.Button(
        frame, text="Anahtar al (tarayıcıda açılır)", command=lambda: webbrowser.open(KEY_URL)
    ).grid(sticky="w", pady=(8, 12))
    ttk.Label(frame, text="Gemini API anahtarı").grid(sticky="w")
    key_var = tk.StringVar()
    key_entry = ttk.Entry(frame, textvariable=key_var, width=52, show="•")
    key_entry.grid(sticky="we", pady=(2, 10))
    ttk.Label(frame, text="Adın (isteğe bağlı, Mario sana böyle hitap eder)").grid(sticky="w")
    name_var = tk.StringVar()
    ttk.Entry(frame, textvariable=name_var, width=52).grid(sticky="we", pady=(2, 14))
    status = ttk.Label(frame, text="", foreground="#b42318")
    status.grid(sticky="w")
    done = {"ok": False}

    def save() -> None:
        key = key_var.get().strip()
        if not key:
            status.config(text="Anahtarı yapıştırmadın.")
            return
        status.config(text="Anahtar deneniyor...", foreground="#555")
        root.update()
        valid = check_key(key)
        if valid is False:
            status.config(
                text="Google bu anahtarı kabul etmedi. Yeniden kopyala.", foreground="#b42318"
            )
            return
        values = {"GEMINI_API_KEY": key}
        if name_var.get().strip():
            values["USER_NAME"] = name_var.get().strip()
        write_env(values, template=RESOURCE_DIR / "backend" / ".env.example")
        if valid is None:
            messagebox.showwarning(
                "Mario", "İnternete ulaşılamadı; anahtar denenmeden kaydedildi.", parent=root
            )
        done["ok"] = True
        root.destroy()

    ttk.Button(frame, text="Kaydet ve başlat", command=save).grid(sticky="e", pady=(10, 0))
    root.bind("<Return>", lambda _e: save())
    key_entry.focus_set()
    root.mainloop()
    return done["ok"]


def prepare_user_dir() -> None:
    """Folders Mario writes to, plus the Claude Code skill it uses when learning.

    Claude Code loads `.claude/skills/` from the folder it runs in (USER_DIR); in
    Mario.exe that is not the source tree, so the skill is copied there with its
    commands pointing at the bundled interpreter and scripts.
    """
    for name in ("skills", "notes", "programs", "claude-workspace"):
        (USER_DIR / name).mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "data").mkdir(parents=True, exist_ok=True)
    if FROZEN:
        src = RESOURCE_DIR / ".claude" / "skills" / "mario-yetenek" / "SKILL.md"
        dst = USER_DIR / ".claude" / "skills" / "mario-yetenek" / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(skill_md_for_exe(src.read_text(encoding="utf-8")), encoding="utf-8")


def skill_md_for_exe(text: str) -> str:
    check = RESOURCE_DIR / "backend" / "scripts" / "check_skill.py"
    py = " ".join([f'& "{PYTHON[0]}"', *PYTHON[1:]])
    text = text.replace(
        r"backend\.venv\Scripts\python backend\scripts\check_skill.py", f'{py} "{check}"'
    )
    return text.replace(r"<proje>\backend\skill_lib", str(RESOURCE_DIR / "backend" / "skill_lib"))


# --- running -------------------------------------------------------------------


def server_up() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def open_window() -> None:
    """Chrome or Edge as an app window (no tabs, no address bar); the default browser otherwise."""
    for exe in ("chrome.exe", "msedge.exe"):
        path = _app_path(exe)
        if path:
            subprocess.Popen([path, f"--app={URL}"])
            return
    webbrowser.open(URL)


def _app_path(exe: str) -> str | None:
    import winreg  # noqa: PLC0415

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with contextlib.suppress(OSError):
            with winreg.OpenKey(
                hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
            ) as k:
                path = winreg.QueryValue(k, None)
                if path and Path(path).is_file():
                    return path
    return None


def _launch_command() -> str:
    if FROZEN:
        return f'"{sys.executable}" --background'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw}" -m app.desktop --background'


def starts_with_windows() -> bool:
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "Mario")
            return True
    except OSError:
        return False


def set_start_with_windows(on: bool) -> None:
    import winreg  # noqa: PLC0415

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Mario", 0, winreg.REG_SZ, _launch_command())
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(k, "Mario")


def logging_config() -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(LOG_FILE),
                "maxBytes": 1_000_000,
                "backupCount": 2,
                "encoding": "utf-8",
                "formatter": "plain",
            }
        },
        "root": {"handlers": ["file"], "level": "INFO"},
        "loggers": {
            name: {"handlers": [], "propagate": True}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }


def icon_image(size: int = 64):
    """The PWA icon (frontend/icons/icon.svg) drawn with Pillow, which can't read SVG."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    s = size / 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=112 * s, fill="#0d1117")
    pts = [
        (160 * s, 352 * s),
        (160 * s, 176 * s),
        (256 * s, 288 * s),
        (352 * s, 176 * s),
        (352 * s, 352 * s),
    ]
    d.line(pts, fill="#e5534b", width=max(2, round(40 * s)), joint="curve")
    r = 20 * s
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill="#e5534b")
    return img


def message(text: str) -> None:
    import ctypes  # noqa: PLC0415

    ctypes.windll.user32.MessageBoxW(None, text, "Mario", 0x40)


def run(background: bool = False) -> int:
    if server_up():
        # Already running (tray icon is there): just bring up a window.
        if not background:
            open_window()
        return 0

    prepare_user_dir()
    if not read_env().get("GEMINI_API_KEY"):
        if background or not ask_for_key():
            return 0

    config = logging_config()
    logging.config.dictConfig(config)

    import pystray  # noqa: PLC0415
    import uvicorn  # noqa: PLC0415

    from .main import app  # noqa: PLC0415  (after the key is written: settings read .env once)

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_config=config, access_log=False)
    )
    failed = threading.Event()

    def serve() -> None:
        try:
            server.run()
        except BaseException:
            log.exception("Server stopped")
        if not server.started:
            failed.set()

    thread = threading.Thread(target=serve, name="mario-server", daemon=True)
    thread.start()
    for _ in range(100):
        if server.started or failed.is_set():
            break
        failed.wait(0.2)
    if not server.started:
        message(f"Mario başlatılamadı. Ayrıntı:\n{LOG_FILE}")
        return 1

    def quit_app(icon, _item=None) -> None:
        server.should_exit = True
        icon.stop()

    def toggle_startup(_icon, _item) -> None:
        set_start_with_windows(not starts_with_windows())

    menu = pystray.Menu(
        pystray.MenuItem("Mario'yu aç", lambda: open_window(), default=True),
        pystray.MenuItem(
            "Windows açılınca başlat", toggle_startup, checked=lambda _i: starts_with_windows()
        ),
        pystray.MenuItem("Ayarlar (.env)", lambda: os.startfile(ENV_FILE)),
        pystray.MenuItem("Mario klasörünü aç", lambda: os.startfile(USER_DIR)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Çıkış", quit_app),
    )
    icon = pystray.Icon("Mario", icon_image(), "Mario", menu)
    if not background:
        open_window()
    icon.run()
    thread.join(timeout=10)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return run(background="--background" in argv)


if __name__ == "__main__":
    sys.exit(main())
