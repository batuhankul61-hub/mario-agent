"""Checks a learned skill the way Mario will load it (used by the mario-yetenek
Claude Code skill).

    python backend/scripts/check_skill.py <name>   # validate skills/<name>/
    python backend/scripts/check_skill.py --list   # built-in tools and learned skills
"""

import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.tools import TOOLS, skills  # noqa: E402

META = {
    "confirm_action",
    "cancel_action",
    "run_skill",
    "list_skills",
    "forget_skill",
    "learn_skill",
}
PS_PARSE = (
    "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:SKILL_FILE, [ref]$null, [ref]$e);"
    ' $e | ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }'
)


def list_all(skills_dir: Path) -> None:
    print("Hazır araçlar:")
    for tool in TOOLS.values():
        if tool.name not in META:
            print(f"- {tool.name}: {tool.description}")
    learned = skills.load_skills(skills_dir)
    print("\nÖğrenilmiş yetenekler:" if learned else "\nÖğrenilmiş yetenek yok.")
    for skill in learned.values():
        print(skill.signature())


def check(folder: Path) -> list[str]:
    try:
        skill = skills.load_skill(folder)
    except skills.InvalidSkill as exc:
        return [f"skill.json: {exc}"]
    problems = []
    if skill.entry.suffix.lower() == ".py":
        try:
            py_compile.compile(str(skill.entry), doraise=True)
        except py_compile.PyCompileError as exc:
            problems.append(f"{skill.entry.name}: {exc.msg}")
    else:
        if not skill.entry.read_bytes().startswith(b"\xef\xbb\xbf"):
            problems.append(
                f"{skill.entry.name}: UTF-8 BOM ile kaydedilmeli (PowerShell 5.1 Türkçe harfleri bozar)."
            )
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_PARSE],
            env={**os.environ, "SKILL_FILE": str(skill.entry)},
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=30,
        ).stdout.strip()
        problems += [f"{skill.entry.name}:{line}" for line in out.splitlines() if line.strip()]
    if skill.confirm:
        unknown = set(re.findall(r"\{(\w+)\}", skill.confirm)) - set(skill.parameters)
        if unknown:
            problems.append(f"confirm, tanımsız parametre kullanıyor: {', '.join(sorted(unknown))}")
    return problems


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    skills_dir = get_settings().skills_dir
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--list":
        list_all(skills_dir)
        return
    problems = check(skills_dir / sys.argv[1])
    if problems:
        print("Sorunlar:\n" + "\n".join(f"- {p}" for p in problems))
        sys.exit(1)
    print(f"Tamam: {sys.argv[1]} Mario tarafından yüklenebilir.")


if __name__ == "__main__":
    main()
