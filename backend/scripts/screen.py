"""Screen helper for Claude's screen tasks (see app/tools/claude.py SCREEN).

screen.py windows | read [--window AD] | click <numara|ad>
screen.py type "metin" [--element X] [--enter] | keys "ctrl+s" | shot [--window AD]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.tools import screen  # noqa: E402


async def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("windows")
    sub.add_parser("read").add_argument("--window", default="")
    sub.add_parser("click").add_argument("element")
    t = sub.add_parser("type")
    t.add_argument("text")
    t.add_argument("--element", default="")
    t.add_argument("--enter", action="store_true")
    sub.add_parser("keys").add_argument("keys")
    sub.add_parser("shot").add_argument("--window", default="")
    a = p.parse_args()
    s = get_settings()

    if a.cmd == "windows":
        windows = [w for w in await screen._call(screen._windows_sync) if not screen._skipped(w)]
        print("\n".join(f"{w['process']}: {w['title']}" for w in windows))
        return 0
    res = await {
        "read": lambda: screen.read_screen({"window": a.window}, s),
        "click": lambda: screen.click_element({"element": a.element}, s),
        "type": lambda: screen.type_text(
            {"text": a.text, "element": a.element, "enter": a.enter}, s
        ),
        "keys": lambda: screen.press_keys({"keys": a.keys}, s),
        "shot": lambda: screen.screenshot_file({"window": a.window}, s),
    }[a.cmd]()
    print(res["message"])
    if res.get("elements"):
        print(res["elements"])
    if res.get("path"):
        print(res["path"])
    return 0 if res["status"] == "ok" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
