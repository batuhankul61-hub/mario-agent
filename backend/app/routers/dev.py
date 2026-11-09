"""Developer view: what Mario has learned, read straight from skills/ and the audit log."""

import datetime as dt
import json
from pathlib import Path

from fastapi import APIRouter, Depends

from ..deps import get_context, require_access
from ..tools import Context
from ..tools.skills import FORGOTTEN, InvalidSkill, load_skill

router = APIRouter(prefix="/api/dev", tags=["dev"], dependencies=[Depends(require_access)])

SOURCE_LIMIT = 64_000


def _mtime(folder: Path) -> str:
    stamps = [p.stat().st_mtime for p in folder.iterdir() if p.is_file()] or [
        folder.stat().st_mtime
    ]
    return dt.datetime.fromtimestamp(max(stamps)).isoformat(timespec="seconds")


def _source(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    return text if len(text) <= SOURCE_LIMIT else text[:SOURCE_LIMIT] + "\n… (kısaltıldı)"


def _usage(audit_log: Path) -> dict[str, dict]:
    """Per-skill run counts from the audit log (run_skill calls that actually ran)."""
    usage: dict[str, dict] = {}
    if not audit_log.exists():
        return usage
    for line in audit_log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("tool") != "run_skill" or entry.get("status") not in ("ok", "error"):
            continue
        name = str((entry.get("args") or {}).get("name", ""))
        u = usage.setdefault(
            name,
            {"runs": 0, "errors": 0, "last_run": None, "last_status": None, "last_message": None},
        )
        u["runs"] += 1
        u["errors"] += entry["status"] == "error"
        u["last_run"], u["last_status"], u["last_message"] = (
            entry.get("ts"),
            entry["status"],
            entry.get("message"),
        )
    return usage


@router.get("/skills")
async def skills(ctx: Context = Depends(get_context)) -> dict:
    skills_dir = ctx.settings.skills_dir
    usage = _usage(ctx.settings.audit_log)
    items: list[dict] = []
    if skills_dir.is_dir():
        for folder in sorted(
            p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
        ):
            base = {
                "name": folder.name,
                "updated": _mtime(folder),
                "usage": usage.get(folder.name, {"runs": 0, "errors": 0}),
            }
            try:
                s = load_skill(folder)
            except InvalidSkill as exc:
                items.append(
                    {
                        **base,
                        "valid": False,
                        "error": str(exc),
                        "manifest": _source(folder / "skill.json"),
                    }
                )
                continue
            items.append(
                {
                    **base,
                    "valid": True,
                    "description": s.description,
                    "parameters": s.parameters,
                    "required": list(s.required),
                    "confirm": s.confirm,
                    "example": s.example,
                    "entry": s.entry.name,
                    "source": _source(s.entry),
                }
            )
    forgotten_dir = skills_dir / FORGOTTEN
    forgotten = (
        sorted(p.name for p in forgotten_dir.iterdir() if p.is_dir())
        if forgotten_dir.is_dir()
        else []
    )
    return {"dir": str(skills_dir), "skills": items, "forgotten": forgotten}
