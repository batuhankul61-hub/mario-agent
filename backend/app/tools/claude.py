"""Hands a spoken task to Claude Code in the background (adapted from
JARVIS-ekler\\claude_isci.py). The tool call returns at once with a job id; the
browser polls /api/jobs/{id} and tells Mario when the job is done."""

import asyncio
import datetime as dt
import json
import secrets
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import audit

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ALLOWED = ["Bash", "PowerShell", "Read", "Glob", "Grep", "Edit", "Write", "WebFetch", "WebSearch"]
# Never allowed, whatever the user confirmed.
DISALLOWED = [
    "Bash(rm -rf:*)", "Bash(format:*)", "Bash(diskpart:*)", "PowerShell(Format-Volume:*)",
    "PowerShell(Clear-Disk:*)", "PowerShell(Remove-Item -Recurse:*)",
]

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
    state: str = "running"  # running | done | failed
    result: str = ""
    started: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    finished: str = ""


class ClaudeJobs:
    def __init__(self, claude_path: str, workdir: Path, timeout_minutes: int, log_path: Path, dry_run: bool = False):
        self.claude_path = claude_path
        self.workdir = workdir
        self.timeout = timeout_minutes * 60
        self.log_path = log_path
        self.dry_run = dry_run
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    async def start(self, task: str) -> dict:
        task = task.strip()
        if not task:
            return {"status": "error", "message": "Görev metni boş."}
        job = Job(id=secrets.token_hex(3), task=task)
        self.jobs[job.id] = job
        t = asyncio.create_task(self._finish(job))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return {"status": "ok", "message": "Görev Claude'a verildi. Bitince haber vereceğim.", "job_id": job.id}

    async def status(self, job_id: str | None) -> dict:
        job = self.jobs.get(job_id) if job_id else next(reversed(self.jobs.values()), None)
        if job is None:
            return {"status": "error", "message": "Claude'a verilmiş bir görev yok."}
        message = {
            "running": "Görev hâlâ sürüyor.",
            "done": f"Görev bitti. {job.result}",
            "failed": f"Görev başarısız oldu. {job.result}",
        }[job.state]
        return {"status": "ok", "message": message, **asdict(job)}

    async def _finish(self, job: Job) -> None:
        ok, text = await asyncio.to_thread(self._run, job.task)
        job.state, job.result = ("done" if ok else "failed"), text
        job.finished = dt.datetime.now().isoformat(timespec="seconds")
        audit.record(self.log_path, tool="claude_job_finished", job_id=job.id, state=job.state, result=text)

    def _run(self, task: str) -> tuple[bool, str]:
        if self.dry_run:
            return True, f"(deneme) Claude şu görevi yapacaktı: {task}"
        self.workdir.mkdir(parents=True, exist_ok=True)
        # The task goes in on stdin, so text like "--help" can't turn into a flag.
        cmd = [
            self.claude_path, "-p", "--output-format", "json", "--append-system-prompt", SYSTEM,
            "--allowedTools", *ALLOWED, "--disallowedTools", *DISALLOWED,
        ]
        try:
            p = subprocess.run(
                cmd, input=task, cwd=self.workdir, capture_output=True, text=True, encoding="utf-8",
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
