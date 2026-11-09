"""Seeing and using the screen through Windows UI Automation, the approach of
microsoft/UFO: Mario reads a window's buttons, fields and links by name, then
clicks and types, instead of guessing from pixels.

Laid out like pc.py: the async tools hold the logic (picking the window,
matching what the user said, safety questions) and call `_sync` helpers that
touch Windows. The helpers run on one worker thread, because UI Automation
objects are COM objects. Tests replace the helpers.

The numbers Mario says ("5'e tıkla") refer to the last read_screen. That list
is saved in data/screen-state.json, so backend/scripts/screen.py, the helper
Claude uses for screen tasks, shares it."""

import asyncio
import ctypes
import datetime as dt
import json
import re
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

from ..config import Settings
from .pc import _windows_sync, err, ok, pick
from .system import plain

MAX_ELEMENTS = 60
MAX_TEXT = 2000
MAX_COMBOS = 10
KEEP_SHOTS = 20
# How far (px) an element may have moved and still count as the one read before.
MOVE_TOLERANCE = 60

# Control types worth naming to the user (UIA_*ControlTypeId).
CONTROL_TYPES = {
    50000: "düğme",
    50002: "onay kutusu",
    50003: "açılır liste",
    50004: "metin alanı",
    50005: "bağlantı",
    50007: "liste öğesi",
    50011: "menü öğesi",
    50013: "seçenek",
    50019: "sekme",
    50024: "ağaç öğesi",
    50030: "belge",
    50031: "bölünmüş düğme",
}
TYPABLE = {"metin alanı", "belge"}

# Windows that are never "the window the user means": the desktop, shell
# overlays, and Mario's own page.
SKIP_PROCESSES = {
    "textinputhost",
    "shellexperiencehost",
    "searchhost",
    "startmenuexperiencehost",
    "lockapp",
}
SKIP_TITLES = {"programmanager"}

# Buttons whose click may be irreversible or outward-facing ask first. Matched
# at the start of a word, after folding Turkish letters ("Gönder" -> "gonder").
RISKY = re.compile(
    r"\b(sil|delete|remove|kaldir|gonder|send|satin|buy|purchase|ode|pay|checkout|siparis|order|onayla|confirm|"
    r"yayinla|publish|post|paylas|share|format|uninstall|bosalt|empty|transfer|abonelik|subscribe)"
)
RISKY_KEYS = {"alt+f4", "shift+delete", "ctrl+w", "ctrl+shift+w", "ctrl+shift+delete"}

VK = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "printscreen": 0x2C,
    "capslock": 0x14,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"},
}
MODIFIERS = {0x10, 0x11, 0x12, 0x5B}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B}


def fold(text: str) -> str:
    """Lowercase, Turkish letters folded, punctuation to spaces: 'Gönder!' -> 'gonder'."""
    text = text.replace("İ", "i").replace("I", "i").lower()
    for a, b in zip("ışğüöç", "isguoc"):
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def is_risky(name: str) -> bool:
    return bool(RISKY.search(fold(name)))


def parse_keys(spec: str) -> list[list[int]]:
    """'ctrl+shift+s enter' -> [[ctrl, shift, S], [enter]]. Raises ValueError on an unknown key."""
    combos = []
    for combo in re.split(r"[\s,]+", spec.strip().lower()):
        if not combo:
            continue
        keys = []
        for key in combo.split("+"):
            if key not in VK:
                raise ValueError(key)
            keys.append(VK[key])
        combos.append(keys)
    return combos


def normalize_keys(spec: str) -> str:
    return " ".join(c for c in re.split(r"[\s,]+", spec.strip().lower()) if c)


# ---------- window and element choice (pure, tested) ----------


def _skipped(w: dict) -> bool:
    return (
        plain(w["process"]) in SKIP_PROCESSES
        or plain(w["title"]) in SKIP_TITLES
        or plain(w["title"]).startswith("mario")
    )


def pick_window(windows: list[dict], name: str = "") -> dict | None:
    """The named window, or else the topmost one that isn't Mario's own page.
    `windows` comes in z-order, topmost first."""
    candidates = [w for w in windows if not _skipped(w)]
    wanted = plain(name)
    if wanted:
        candidates = [
            w for w in candidates if wanted in plain(w["process"]) or wanted in plain(w["title"])
        ]
    return candidates[0] if candidates else None


def resolve(ref, elements: list[dict]) -> tuple[dict | None, str, str]:
    """Finds the element the user means by number or name.
    Returns (element, "", "") or (None, status, message)."""
    if not elements:
        return None, "error", "Önce ekrana bakmam gerekiyor (read_screen)."
    ref = str(ref if ref is not None else "").strip().rstrip(".")
    if not ref:
        return None, "error", "Hangi öğe? Numarasını ya da adını söyle."
    if ref.isdigit():
        n = int(ref)
        if 1 <= n <= len(elements):
            return elements[n - 1], "", ""
        return (
            None,
            "error",
            f"{n} numaralı öğe yok; 1 ile {len(elements)} arasında bir numara söyle.",
        )
    key, candidates = pick(ref, {str(i): e["name"] for i, e in enumerate(elements) if e["name"]})
    if key is not None:
        return elements[int(key)], "", ""
    if candidates:
        return (
            None,
            "ambiguous",
            f"Birden fazla öğe uyuyor: {', '.join(dict.fromkeys(candidates))}. Hangisi?",
        )
    return None, "error", f"Ekranda '{ref}' adında bir öğe bulamadım. Ekrana yeniden bakayım mı?"


def describe(elements: list[dict]) -> str:
    return "\n".join(
        f"{i}. {e['name'] or '(adsız)'} ({e['type']})" for i, e in enumerate(elements, 1)
    )


# ---------- state shared with backend/scripts/screen.py ----------


def _state_path(settings: Settings) -> Path:
    return settings.data_dir / "screen-state.json"


def load_state(settings: Settings) -> dict:
    try:
        return json.loads(_state_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(settings: Settings, state: dict) -> None:
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------- tools ----------


async def _target(args: dict, settings: Settings) -> tuple[dict | None, dict]:
    """The window to act on: the named one, else the one last read if still open, else the topmost."""
    windows = await _call(_windows_sync)
    state = load_state(settings)
    if args.get("window"):
        return pick_window(windows, args["window"]), state
    last = next((w for w in windows if w["hwnd"] == state.get("hwnd")), None)
    return last or pick_window(windows), state


async def read_screen(args: dict, settings: Settings) -> dict:
    # Always the window in front now (or the named one), not the one read last time.
    name = args.get("window", "")
    window = pick_window(await _call(_windows_sync), name)
    if window is None:
        return err(
            f"'{name}' adında açık bir pencere bulamadım."
            if name
            else "Ekranda açık bir pencere yok."
        )
    elements = (await _call(_elements_sync, window["hwnd"]))[:MAX_ELEMENTS]
    save_state(
        settings,
        {
            "hwnd": window["hwnd"],
            "title": window["title"],
            "process": window["process"],
            "elements": elements,
        },
    )
    if not elements:
        return ok(
            f"{window['title']} penceresinde adı okunabilen bir öğe bulamadım; bu uygulama çizimle çalışıyor "
            "olabilir. Gerekirse screen_task ile Claude ekran görüntüsüne bakarak yapabilir.",
            window=window["title"],
            elements="",
        )
    return ok(
        f"{window['title']} penceresinde {len(elements)} öğe var.",
        window=window["title"],
        elements=describe(elements),
    )


def click_question(args: dict, settings: Settings) -> str | None:
    element, _, _ = resolve(args.get("element"), load_state(settings).get("elements") or [])
    if element and is_risky(element["name"]):
        return f"Ekranda «{element['name']}» öğesine basılsın mı? Bu geri alınamayabilir."
    return None


async def click_element(args: dict, settings: Settings) -> dict:
    state = load_state(settings)
    element, status, problem = resolve(args.get("element"), state.get("elements") or [])
    if element is None:
        return {"status": status, "message": problem}
    label = element["name"] or element["type"]
    if settings.dry_run:
        return ok(f"(deneme) «{label}» öğesine tıklanacaktı.")
    how = await _call(_click_sync, state["hwnd"], element)
    if not how:
        return err("Ekran değişmiş, o öğeyi artık bulamıyorum. Ekrana yeniden bakayım mı?")
    return ok(f"«{label}» öğesine tıklandı.", how=how)


async def type_text(args: dict, settings: Settings) -> dict:
    text = str(args.get("text") or "")
    if not text:
        return err("Yazılacak metin boş.")
    if len(text) > MAX_TEXT:
        return err(f"Metin çok uzun; en fazla {MAX_TEXT} karakter yazabilirim.")
    window, state = await _target({}, settings)
    element = None
    if args.get("element") not in (None, ""):
        element, status, problem = resolve(args["element"], state.get("elements") or [])
        if element is None:
            return {"status": status, "message": problem}
        window = {"hwnd": state["hwnd"], "title": state.get("title", "")}
    if window is None:
        return err("Yazabileceğim açık bir pencere yok.")
    enter = bool(args.get("enter"))
    if settings.dry_run:
        return ok(
            f"(deneme) {window['title']} penceresine yazılacaktı{' ve Enter basılacaktı' if enter else ''}."
        )
    if element and not await _call(_click_sync, window["hwnd"], element):
        return err("Ekran değişmiş, yazılacak alanı bulamıyorum. Ekrana yeniden bakayım mı?")
    if not await _call(_type_sync, window["hwnd"], text, enter):
        return err(
            f"{window['title']} penceresini öne getiremedim; yanlış yere yazmamak için durdum."
        )
    return ok(f"{window['title']} penceresine yazıldı{' ve Enter basıldı' if enter else ''}.")


def keys_question(args: dict) -> str | None:
    risky = [c for c in normalize_keys(str(args.get("keys") or "")).split() if c in RISKY_KEYS]
    return (
        f"{', '.join(risky)} basılsın mı? Pencere ya da sekme kapanabilir, kaydedilmemiş iş kaybolabilir."
        if risky
        else None
    )


async def press_keys(args: dict, settings: Settings) -> dict:
    spec = str(args.get("keys") or "")
    try:
        combos = parse_keys(spec)
    except ValueError as exc:
        return err(f"'{exc}' tuşunu tanımıyorum. Ör. ctrl+s, enter, alt+tab, f5.")
    if not combos:
        return err("Hangi tuşlar?")
    if len(combos) > MAX_COMBOS:
        return err(f"Tek seferde en fazla {MAX_COMBOS} tuş kombinasyonu basabilirim.")
    window, _ = await _target({"window": args.get("window", "")}, settings)
    if window is None:
        return err("Tuşlara basabileceğim açık bir pencere yok.")
    shown = normalize_keys(spec)
    if settings.dry_run:
        return ok(f"(deneme) {window['title']} penceresinde {shown} basılacaktı.")
    if not await _call(_keys_sync, window["hwnd"], combos):
        return err(f"{window['title']} penceresini öne getiremedim; tuşlara basmadım.")
    return ok(f"{window['title']} penceresinde {shown} basıldı.")


async def screenshot_file(args: dict, settings: Settings) -> dict:
    """A PNG of the screen (or a window) for Claude to look at; not shown to the user."""
    hwnd = None
    if args.get("window"):
        window, _ = await _target({"window": args["window"]}, settings)
        if window is None:
            return err(f"'{args['window']}' adında açık bir pencere bulamadım.")
        hwnd = window["hwnd"]
    folder = settings.data_dir / "screen"
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob("shot-*.png"))[:-KEEP_SHOTS]:
        old.unlink(missing_ok=True)
    path = folder / f"shot-{dt.datetime.now():%Y%m%d-%H%M%S-%f}.png"
    await _call(_capture_sync, path, hwnd)
    return ok("Ekran görüntüsü alındı.", path=str(path))


# ---------- Windows helpers (replaced in tests) ----------


def _init_thread() -> None:
    try:
        import comtypes  # noqa: PLC0415  # Windows only

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        # Physical pixels everywhere, so UI Automation rectangles match the cursor.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (ImportError, OSError, AttributeError):
        pass


_pool = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="mario-screen", initializer=_init_thread
)


async def _call(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_pool, fn, *args)


_uia = None


def _automation():
    global _uia
    if _uia is None:
        from comtypes.client import CreateObject, GetModule  # noqa: PLC0415

        GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA  # noqa: PLC0415

        _uia = (CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation), UIA)
    return _uia


def _scan(hwnd: int) -> list[tuple[dict, object]]:
    """Named, visible, enabled controls of a window, in reading order."""
    from comtypes import COMError  # noqa: PLC0415

    auto, _ = _automation()
    root = auto.ElementFromHandle(hwnd)
    cond = None
    for ctype in CONTROL_TYPES:
        c = auto.CreatePropertyCondition(30003, ctype)  # UIA_ControlTypePropertyId
        cond = c if cond is None else auto.CreateOrCondition(cond, c)
    found = root.FindAll(4, cond)  # TreeScope_Descendants
    out = []
    for i in range(found.Length):
        e = found.GetElement(i)
        try:
            if e.CurrentIsOffscreen or not e.CurrentIsEnabled:
                continue
            r = e.CurrentBoundingRectangle
            if r.right <= r.left or r.bottom <= r.top:
                continue
            kind = CONTROL_TYPES.get(e.CurrentControlType, "öğe")
            name = " ".join((e.CurrentName or "").split())[:80]
            if not name and kind not in TYPABLE:
                continue
            out.append(
                ({"name": name, "type": kind, "rect": [r.left, r.top, r.right, r.bottom]}, e)
            )
        except COMError:
            continue
        if len(out) >= MAX_ELEMENTS:
            break
    return out


def _elements_sync(hwnd: int) -> list[dict]:
    return [info for info, _ in _scan(hwnd)]


def _center(rect: list[int]) -> tuple[int, int]:
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


def _click_sync(hwnd: int, element: dict) -> str:
    """Clicks the element read before, found again by name, type and place.
    Returns how ("invoke" or "mouse"), or "" when it is gone."""
    from comtypes import COMError  # noqa: PLC0415

    _, UIA = _automation()
    cx, cy = _center(element["rect"])
    best, best_d = None, MOVE_TOLERANCE + 1
    for info, e in _scan(hwnd):
        if info["name"] == element["name"] and info["type"] == element["type"]:
            x, y = _center(info["rect"])
            d = abs(x - cx) + abs(y - cy)
            if d < best_d:
                best, best_d = (info, e), d
    if best is None:
        return ""
    info, e = best
    if info["type"] not in TYPABLE:
        for pattern_id, iface, method in (
            (10000, UIA.IUIAutomationInvokePattern, "Invoke"),
            (10015, UIA.IUIAutomationTogglePattern, "Toggle"),
            (10010, UIA.IUIAutomationSelectionItemPattern, "Select"),
            (10005, UIA.IUIAutomationExpandCollapsePattern, "Expand"),
        ):
            try:
                pattern = e.GetCurrentPattern(pattern_id)
                if pattern:
                    getattr(pattern.QueryInterface(iface), method)()
                    return "invoke"
            except COMError:
                continue
    if not _focus_sync(hwnd):
        return ""
    try:
        e.SetFocus()
    except COMError:
        pass
    _mouse_click(*_center(info["rect"]))
    return "mouse"


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4


def _send(inputs: list[_INPUT]) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
    if vk in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    return _INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)))


def _mouse_click(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(x, y)
    _send(
        [
            _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(dwFlags=0x2))),  # left down
            _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(dwFlags=0x4))),
        ]
    )  # left up


def _focus_sync(hwnd: int) -> bool:
    """Brings the window to the front. Windows only lets the foreground thread
    do that, so this thread briefly shares its input state."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    me, them = kernel32.GetCurrentThreadId(), user32.GetWindowThreadProcessId(fg, None)
    user32.AttachThreadInput(me, them, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(me, them, False)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        kernel32.Sleep(25)
    return False


def _type_sync(hwnd: int, text: str, enter: bool) -> bool:
    if not _focus_sync(hwnd):
        return False
    inputs = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            inputs += [_key(0x0D), _key(0x0D, flags=KEYEVENTF_KEYUP)]
            continue
        units = ch.encode("utf-16-le")
        for i in range(0, len(units), 2):
            unit = int.from_bytes(units[i : i + 2], "little")
            inputs += [
                _key(scan=unit, flags=KEYEVENTF_UNICODE),
                _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            ]
    if enter:
        inputs += [_key(0x0D), _key(0x0D, flags=KEYEVENTF_KEYUP)]
    for i in range(0, len(inputs), 200):
        _send(inputs[i : i + 200])
    return True


def _keys_sync(hwnd: int, combos: list[list[int]]) -> bool:
    if not _focus_sync(hwnd):
        return False
    for combo in combos:
        held = [k for k in combo if k in MODIFIERS]
        rest = [k for k in combo if k not in MODIFIERS] or held[-1:]
        held = [k for k in held if k not in rest]
        _send(
            [_key(k) for k in held]
            + [x for k in rest for x in (_key(k), _key(k, flags=KEYEVENTF_KEYUP))]
            + [_key(k, flags=KEYEVENTF_KEYUP) for k in reversed(held)]
        )
        ctypes.windll.kernel32.Sleep(60)
    return True


def png(width: int, height: int, bgra: bytes) -> bytes:
    """Encodes 32-bit BGRA pixels, top row first, as an RGB PNG (standard library only)."""
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * stride : (y + 1) * stride]) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _capture_sync(path: Path, hwnd: int | None) -> None:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    handle = ctypes.c_void_p
    user32.GetDC.restype = handle
    user32.GetDC.argtypes = [handle]
    user32.ReleaseDC.argtypes = [handle, handle]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.BitBlt.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        handle,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.GetDIBits.argtypes = [
        handle,
        handle,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteDC.argtypes = [handle]

    if hwnd:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        x, y, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
    else:  # the whole virtual screen, every monitor
        x, y, w, h = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    try:
        gdi32.SelectObject(mem, bmp)
        gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020 | 0x40000000)  # SRCCOPY | CAPTUREBLT
        info = _BITMAPINFOHEADER(
            biSize=ctypes.sizeof(_BITMAPINFOHEADER),
            biWidth=w,
            biHeight=-h,
            biPlanes=1,
            biBitCount=32,
            biCompression=0,
        )
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    path.write_bytes(png(w, h, buf.raw))
