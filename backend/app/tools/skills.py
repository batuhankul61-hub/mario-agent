"""Skills Mario learned through Claude Code (the idea comes from JARVIS-ekler\\
claude_isci.py --yetenek).

Each skill is a folder `skills/<name>/` with a `skill.json` manifest and one
script (`run.ps1` or `run.py`). The script gets its arguments as a JSON object
on stdin and prints one short Turkish sentence; a line starting with "Hata:"
means it failed. Skills are read from disk on every call, so a skill Claude has
just written is usable at once, even inside the voice session that asked for it
(the Live token locks the tool list, so skills go through one generic
`run_skill` tool instead of a declaration each).

Manifest:
    {
      "name": "whatsapp_mesaj",
      "description": "WhatsApp Desktop'tan bir kişiye mesaj gönderir.",
      "parameters": {"kisi": "Kişinin adı ya da numarası", "mesaj": "Gönderilecek metin"},
      "required": ["kisi", "mesaj"],
      "confirm": "{kisi} kişisine şu mesaj gönderilsin mi: {mesaj}",   // null when harmless
      "entry": "run.ps1",
      "example": "WhatsApp'tan Ahmet'e geliyorum yaz"
    }
"""

import asyncio
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import BACKEND_DIR, Settings

SKILL_LIB = BACKEND_DIR / "skill_lib"

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
ENTRIES = {".ps1", ".py"}
RUN_TIMEOUT = 30
FORGOTTEN = "_unutulan"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class InvalidSkill(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    folder: Path
    entry: Path
    parameters: dict[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    confirm: str | None = None
    example: str = ""

    def question(self, args: dict) -> str | None:
        if not self.confirm:
            return None
        # Plain {name} substitution only; str.format would allow attribute access.
        return re.sub(r"\{(\w+)\}", lambda m: str(args.get(m.group(1), "")), self.confirm)

    def signature(self) -> str:
        params = ", ".join(f"{k}: {v}" for k, v in self.parameters.items())
        flag = " (onay ister)" if self.confirm else ""
        return f"- {self.name}({params}): {self.description}{flag}"


def load_skill(folder: Path) -> Skill:
    try:
        data = json.loads((folder / "skill.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InvalidSkill(f"skill.json okunamadı: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidSkill("skill.json bir JSON nesnesi olmalı.")
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name) or name != folder.name:
        raise InvalidSkill(
            "name, klasör adıyla aynı olmalı ve küçük harf, rakam, alt çizgiden oluşmalı."
        )
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise InvalidSkill("description boş olamaz.")
    params = data.get("parameters") or {}
    if not isinstance(params, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in params.items()
    ):
        raise InvalidSkill("parameters, {ad: açıklama} biçiminde olmalı.")
    required = data.get("required") or []
    if not isinstance(required, list) or not set(required) <= set(params):
        raise InvalidSkill("required yalnızca parameters'taki adları içerebilir.")
    confirm = data.get("confirm")
    if confirm is not None and (not isinstance(confirm, str) or not confirm.strip()):
        raise InvalidSkill("confirm ya null ya da bir soru metni olmalı.")
    entry_name = data.get("entry")
    entry = (folder / str(entry_name)).resolve() if entry_name else None
    if (
        not entry
        or entry.parent != folder.resolve()
        or entry.suffix.lower() not in ENTRIES
        or not entry.is_file()
    ):
        raise InvalidSkill("entry, klasördeki bir .ps1 ya da .py dosyası olmalı.")
    return Skill(
        name,
        description.strip(),
        folder,
        entry,
        params,
        tuple(required),
        confirm,
        str(data.get("example") or ""),
    )


def load_skills(skills_dir: Path) -> dict[str, Skill]:
    found: dict[str, Skill] = {}
    if not skills_dir.is_dir():
        return found
    for folder in sorted(
        p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    ):
        try:
            found[folder.name] = load_skill(folder)
        except InvalidSkill as exc:
            log.warning("Skipping skill %s: %s", folder.name, exc)
    return found


def prompt_section(skills_dir: Path) -> str:
    skills = load_skills(skills_dir)
    if not skills:
        return ""
    lines = "\n".join(s.signature() for s in skills.values())
    return f"\n\nÖğrenilmiş yetenekler (run_skill ile kullan, args_json parametreleri JSON nesnesi olarak alır):\n{lines}"


def parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError("args_json geçerli bir JSON nesnesi değil.") from exc
    if not isinstance(value, dict):
        raise ValueError("args_json bir JSON nesnesi olmalı.")
    return value


def skill_question(args: dict, settings: Settings) -> str | None:
    skill = load_skills(settings.skills_dir).get(str(args.get("name", "")))
    try:
        return skill.question(parse_args(args.get("args_json"))) if skill else None
    except ValueError:
        return None  # run_skill itself reports the bad arguments


def _command(skill: Skill) -> list[str]:
    if skill.entry.suffix.lower() == ".ps1":
        # Windows PowerShell reads stdin in the OEM code page, so Turkish letters in the JSON
        # (Şımarık, ğ, ı) arrive garbled unless stdin is switched to UTF-8 before the script runs.
        path = str(skill.entry).replace("'", "''")
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "[Console]::InputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false; "
            # A terminating error becomes a "Hata:" line, the protocol's failure signal.
            f"try {{ & '{path}' }} catch {{ Write-Output ('Hata: ' + $_.Exception.Message); exit 1 }}; "
            "exit $LASTEXITCODE",
        ]
    # -I: isolated mode, so the skill folder can't shadow standard modules. It also ignores
    # PYTHONIOENCODING, hence -X utf8 for the Turkish text on stdin/stdout.
    return [sys.executable, "-I", "-X", "utf8", str(skill.entry)]


def _execute(skill: Skill, args: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        _command(skill),
        input=json.dumps(args, ensure_ascii=False),
        cwd=skill.folder,
        # MARIO_LIB: shared helpers (backend/skill_lib), e.g. window.ps1 for
        # bringing a window to the front reliably.
        env={**os.environ, "MARIO_LIB": str(SKILL_LIB)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT,
        creationflags=NO_WINDOW,
    )


async def run_skill(args: dict, settings: Settings) -> dict:
    name = str(args.get("name", ""))
    skill = load_skills(settings.skills_dir).get(name)
    if skill is None:
        return {"status": "error", "message": f"'{name}' adında öğrenilmiş bir yetenek yok."}
    try:
        skill_args = parse_args(args.get("args_json"))
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    missing = [p for p in skill.required if not str(skill_args.get(p, "")).strip()]
    if missing:
        return {"status": "error", "message": f"Eksik bilgi: {', '.join(missing)}."}
    if settings.dry_run:
        return {"status": "ok", "message": f"(deneme) {name} yeteneği çalışacaktı."}
    result = await _run(skill, skill_args)
    streak = record_health(settings.data_dir / HEALTH_FILE, name, result)
    if result["status"] == "error" and streak >= REPAIR_AFTER:
        result["needs_repair"] = True
        result["message"] += (
            f" Bu yetenek üst üste {streak} kez başarısız oldu; kullanıcıya Claude'un onarmasını "
            "isteyip istemediğini sor."
        )
    return result


HEALTH_FILE = "skill-health.json"
# Failures in a row before Mario offers to have Claude repair the skill.
REPAIR_AFTER = 3


def load_health(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_health(path: Path, name: str, result: dict) -> int:
    """Counts runs and failures per skill; returns the current failure streak."""
    data = load_health(path)
    h = data.setdefault(name, {"runs": 0, "fails": 0, "streak": 0})
    now = dt.datetime.now().isoformat(timespec="seconds")
    h["runs"] += 1
    if result["status"] == "ok":
        h["streak"] = 0
        h["last_ok"] = now
    else:
        h["fails"] += 1
        h["streak"] += 1
        h["last_error"] = result["message"][:200]
        h["last_error_at"] = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        log.warning("Could not save skill health", exc_info=True)
    return h["streak"]


async def _run(skill: Skill, skill_args: dict) -> dict:
    name = skill.name
    try:
        proc = await asyncio.to_thread(_execute, skill, skill_args)
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"{name} {RUN_TIMEOUT} saniyede bitmedi."}
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    # In a .ps1 that ends without `exit`, $LASTEXITCODE is whatever native
    # command ran last (e.g. a `winget list` that found nothing), so its exit
    # code is unreliable; the "Hata:" line is what counts there.
    exit_failed = proc.returncode != 0 and skill.entry.suffix.lower() != ".ps1"
    if exit_failed or not last or last.startswith("Hata"):
        stderr = proc.stderr.strip().splitlines()
        detail = last or (stderr[-1] if stderr else f"çıkış kodu {proc.returncode}")
        return {"status": "error", "message": f"{name}: {detail.removeprefix('Hata: ')}"}
    return {"status": "ok", "message": last}


async def list_skills(args: dict, settings: Settings) -> dict:
    skills = load_skills(settings.skills_dir)
    if not skills:
        return {"status": "ok", "message": "Henüz öğrendiğim bir yetenek yok.", "skills": []}
    health = load_health(settings.data_dir / HEALTH_FILE)
    broken = [n for n in skills if health.get(n, {}).get("streak", 0) >= REPAIR_AFTER]
    return {
        "status": "ok",
        "message": "Öğrendiğim yetenekler: "
        + "; ".join(f"{s.name}: {s.description}" for s in skills.values())
        + (f". Son denemelerde hep başarısız olanlar: {', '.join(broken)}." if broken else ""),
        "skills": [s.signature() for s in skills.values()],
        "health": {n: health[n] for n in skills if n in health},
    }


async def forget_skill(args: dict, settings: Settings) -> dict:
    """Moves the skill to skills/_unutulan/ instead of deleting it, so it can be restored."""
    name = str(args.get("name", ""))
    skill = load_skills(settings.skills_dir).get(name)
    if skill is None:
        return {"status": "error", "message": f"'{name}' adında bir yetenek yok."}
    if settings.dry_run:
        return {"status": "ok", "message": f"(deneme) {name} unutulacaktı."}
    target = settings.skills_dir / FORGOTTEN / f"{name}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, str(skill.folder), str(target))
    return {
        "status": "ok",
        "message": f"{name} yeteneğini unuttum. Geri almak için klasörü {target} konumundan geri taşıyabilirsin.",
    }
