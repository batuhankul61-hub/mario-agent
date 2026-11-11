"""Entry point of Mario.exe (built by packaging/build.ps1).

    Mario.exe                      the app: first-run setup, server, tray icon
    Mario.exe --background         same, without opening a window (start with Windows)
    Mario.exe python script.py …   runs a Python file with the bundled interpreter

The last form stands in for python.exe, which the bundle doesn't have: learned
skills (run.py), check_skill.py and Claude's screen helper run through it.
"""

import os
import sys


def _stdio() -> None:
    # A windowed exe has sys.std* = None without a parent's pipes, and the ANSI code
    # page (cp1254) with them; skills and Mario speak UTF-8 (python.exe -X utf8).
    for fd, name, mode in ((0, "stdin", "r"), (1, "stdout", "w"), (2, "stderr", "w")):
        stream = getattr(sys, name)
        if stream is not None:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
                continue
            except (AttributeError, ValueError, OSError):
                pass
            try:
                stream = open(fd, mode, encoding="utf-8", errors="replace", closefd=False)  # noqa: SIM115
            except OSError:
                stream = open(os.devnull, mode, encoding="utf-8")  # noqa: SIM115
            setattr(sys, name, stream)


def _run_python(args: list[str]) -> int:
    import runpy

    # Interpreter flags the callers pass to a real python.exe (-I, -X utf8): this
    # one is already isolated from PYTHON* variables and reads/writes UTF-8.
    while args and args[0].startswith("-"):
        flag = args.pop(0)
        if flag in ("-X", "-W") and args:
            args.pop(0)
    if not args:
        print("Kullanım: Mario.exe python <dosya.py> [argümanlar]", file=sys.stderr)
        return 2
    script = os.path.abspath(args[0])
    sys.argv = [script, *args[1:]]
    sys.path.insert(0, os.path.dirname(script))
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code
        if code is None or isinstance(code, int):
            return code or 0
        print(code, file=sys.stderr)
        return 1
    finally:
        for s in (sys.stdout, sys.stderr):
            s.flush()
    return 0


def main() -> int:
    _stdio()
    if len(sys.argv) > 1 and sys.argv[1] == "python":
        return _run_python(sys.argv[2:])
    from app.desktop import main as desktop_main

    return desktop_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
