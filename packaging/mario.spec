# PyInstaller spec for Mario.exe; build with packaging/build.ps1.
# A folder build (onedir): Mario.exe also runs learned skills (`Mario.exe python run.py`),
# and a one-file exe would unpack itself to %TEMP% on every one of those runs.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"

datas = [
    (str(ROOT / "frontend"), "frontend"),
    (str(BACKEND / "skill_lib"), "backend/skill_lib"),
    (str(BACKEND / ".env.example"), "backend"),
    (str(ROOT / ".claude" / "skills" / "mario-yetenek"), ".claude/skills/mario-yetenek"),
    (str(ROOT / "packaging" / "mario.ico"), "packaging"),
]
# Scripts run with `Mario.exe python …` (screen helper, skill checker) and screenshot.ps1.
for f in (BACKEND / "scripts").iterdir():
    if f.suffix in {".py", ".ps1"}:
        datas.append((str(f), "backend/scripts"))

binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("uvicorn")
# Packages with data files, native parts or imports PyInstaller can't follow.
for pkg in ("winrt", "pycaw", "comtypes", "google.genai", "google.adk", "screen_brightness_control", "pystray"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# collect_all also pulls in the packages' own test suites.
hiddenimports = [m for m in hiddenimports if ".tests" not in m and ".test." not in m and not m.endswith(".test")]

a = Analysis(
    [str(ROOT / "packaging" / "mario_exe.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "ruff", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mario",
    icon=str(ROOT / "packaging" / "mario.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Mario", upx=False)
