"""Hands a spoken task to Claude Code in the background (adapted from
JARVIS-ekler\\claude_isci.py). The tool call returns at once with a job id; the
browser polls /api/jobs/{id} and tells Mario when the job is done.

Two kinds of job: a one-off task (delegate_to_claude), and teaching Mario a new
skill (learn_skill), where Claude writes skills/<name>/ following the
mario-yetenek Claude Code skill in .claude/skills/."""

import asyncio
import datetime as dt
import json
import re
import secrets
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import audit
from . import skills

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ALLOWED = ["Bash", "PowerShell", "Read", "Glob", "Grep", "Edit", "Write", "WebFetch", "WebSearch"]
# Never allowed, whatever the user confirmed.
DISALLOWED = [
    "Bash(rm -rf:*)", "Bash(format:*)", "Bash(diskpart:*)", "PowerShell(Format-Volume:*)",
    "PowerShell(Clear-Disk:*)", "PowerShell(Remove-Item -Recurse:*)",
]

# Learning a skill: Claude works in the project folder (so it loads the
# mario-yetenek skill) and must only write under skills/.
LEARN_ALLOWED = ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "PowerShell", "WebFetch", "WebSearch", "Skill"]
LEARN_DISALLOWED = DISALLOWED + [
    f"{tool}(./{folder}/**)" for tool in ("Edit", "Write") for folder in ("backend", "frontend", ".claude")
]

LEARN = """Mario sesli asistanına kullanıcıdan şu istek geldi, ama Mario'nun bunu yapacak bir aracı ya da yeteneği yok:
«{request}»

Görevin bu işi Mario'ya kalıcı olarak öğretmek. mario-yetenek skill'ini kullan (.claude/skills/mario-yetenek/SKILL.md)
ve oradaki kurallara uy. Kullanıcıya soru soramazsın; kullanıcının isteğini kendin yerine getirme, onu Mario yapacak.

Son mesajın yalnızca şu iki satır olsun, başka hiçbir şey yazma:
SONUÇ: <Mario'nun sesli söyleyeceği tek kısa Türkçe cümle: ne öğrendiği ya da neden öğrenemediği>
YETENEK: <yazdığın ya da zaten var olan yeteneğin adı; öğretemediysen: yok>"""

SYSTEM = (
    "Bu görev, kullanıcının sesli asistanı Mario'dan geliyor; kullanıcı görevi sesle verdi ve onayladı, ama sen "
    "ona soru soramazsın. Görevi kendin tamamla. Görevde açıkça istenmeyen hiçbir şeyi silme, gönderme ya da "
    "satın alma. Bir şey belirsizse en güvenli yolu seç ya da yapmadan nedenini söyle. Son mesajın Mario'nun "
    "sesli okuyacağı kısa bir Türkçe özet olsun: bir iki cümle, liste ve markdown yok."
)


@dataclass
class Job:
    id: str
    task: str
    kind: str = "task"  # task | skill
    state: str = "running"  # running | done | failed
    result: str = ""
    skill: dict | None = None  # for kind "skill": what Mario can now call
    started: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    finished: str = ""


class ClaudeJobs:
    def __init__(self, claude_path: str, workdir: Path, timeout_minutes: int, log_path: Path, dry_run: bool = False,
                 project_dir: Path | None = None, skills_dir: Path | None = None):
        self.claude_path = claude_path
        self.workdir = workdir
        self.project_dir = project_dir or workdir
        self.skills_dir = skills_dir or workdir / "skills"
        self.timeout = timeout_minutes * 60
        self.log_path = log_path
        self.dry_run = dry_run
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self, task: str, kind: str = "task") -> dict:
        task = task.strip()
        if not task:
            return {"status": "error", "message": "Görev metni boş."}
        job = Job(id=secrets.token_hex(3), task=task, kind=kind)
        self.jobs[job.id] = job
        t = asyncio.create_task(self._finish(job))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        message = (
            "Öğrenmeye başladım. Claude bir iki dakika içinde bana bu yeteneği öğretecek, bitince haber vereceğim."
            if kind == "skill" else "Görev Claude'a verildi. Bitince haber vereceğim."
        )
        return {"status": "ok", "message": message, "job_id": job.id, "kind": kind}

    async def learn(self, request: str) -> dict:
        return await self.start(request, kind="skill")

    async def status(self, job_id: str | None) -> dict:
        job = self.jobs.get(job_id) if job_id else next(reversed(self.jobs.values()), None)
        if job is None:
            return {"status": "error", "message": "Claude'a verilmiş bir görev yok."}
        what = "Öğrenme" if job.kind == "skill" else "Görev"
        message = {
            "running": f"{what} hâlâ sürüyor.",
            "done": f"{what} bitti. {job.result}",
            "failed": f"{what} başarısız oldu. {job.result}",
        }[job.state]
        return {"status": "ok", "message": message, **asdict(job)}

    async def _finish(self, job: Job) -> None:
        if job.kind == "skill":
            ok, text = await asyncio.to_thread(
                self._run, LEARN.format(request=job.task), self.project_dir, LEARN_ALLOWED, LEARN_DISALLOWED
            )
            ok, text, job.skill = self._learned(ok, text)
        else:
            ok, text = await asyncio.to_thread(self._run, job.task, self.workdir, ALLOWED, DISALLOWED)
        job.state, job.result = ("done" if ok else "failed"), text
        job.finished = dt.datetime.now().isoformat(timespec="seconds")
        audit.record(self.log_path, tool="claude_job_finished", job_id=job.id, kind=job.kind, state=job.state,
                     result=text, skill=job.skill and job.skill["name"])

    def _learned(self, ok: bool, text: str) -> tuple[bool, str, dict | None]:
        """Checks Claude's two-line answer against what is really on disk."""
        if not ok:
            return False, text, None
        said = line(text, "SONUÇ") or text[-300:]
        name = line(text, "YETENEK").strip("` ").lower()
        if not name or name == "yok":
            return False, said, None
        try:
            skill = skills.load_skill(self.skills_dir / name)
        except skills.InvalidSkill as exc:
            return False, f"Claude bir yetenek yazdı ama kullanılamıyor: {exc}", None
        return True, said, {
            "name": skill.name, "description": skill.description, "parameters": skill.parameters,
            "required": list(skill.required), "confirm": bool(skill.confirm), "example": skill.example,
        }

    def _run(self, prompt: str, cwd: Path, allowed: list[str], disallowed: list[str]) -> tuple[bool, str]:
        if self.dry_run:
            return True, f"(deneme) Claude şunu yapacaktı: {prompt}"
        cwd.mkdir(parents=True, exist_ok=True)
        # The prompt goes in on stdin, so text like "--help" can't turn into a flag.
        cmd = [
            self.claude_path, "-p", "--output-format", "json", "--append-system-prompt", SYSTEM,
            "--allowedTools", *allowed, "--disallowedTools", *disallowed,
        ]
        try:
            p = subprocess.run(
                cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return False, f"Görev {self.timeout // 60} dakikada bitmedi, durdurdum."
        except OSError as exc:
            return False, f"Claude Code çalıştırılamadı ({exc.__class__.__name__}); kurulu olmayabilir."
        try:
            out = json.loads(p.stdout)
            ok, text = not out.get("is_error"), (out.get("result") or "").strip() or "Sonuç boş döndü."
        except ValueError:
            ok, text = False, (p.stdout + p.stderr).strip()[-400:] or f"Claude çıkış kodu {p.returncode}."
        if not ok and ("not logged in" in text.lower() or "/login" in text):
            text = "Bu bilgisayardaki Claude Code'da oturum açılmamış. Bir terminalde claude yazıp /login yapılmalı."
        return ok, text


def line(text: str, label: str) -> str:
    """Value of a "LABEL: value" line; Claude sometimes writes **LABEL:** `value`."""
    m = re.search(rf"^\W*{label}\W*:\s*(.+)$", text, re.M)
    return m.group(1).strip(" *`") if m else ""
