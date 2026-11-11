"""Runtime settings, read from environment variables or backend/.env."""

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent

# Mario.exe (PyInstaller, see packaging/): the files that ship with it are read
# from the bundle, the user's own (.env, data, skills, notes) from %LOCALAPPDATA%.
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    USER_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Mario"
    STATE_DIR = USER_DIR
else:
    RESOURCE_DIR = ROOT_DIR  # frontend/, backend/scripts, backend/skill_lib, .claude/
    USER_DIR = ROOT_DIR  # skills/, notes/, programs/, claude-workspace/
    STATE_DIR = BACKEND_DIR  # .env, data/, gmail-client.json
ENV_FILE = STATE_DIR / ".env"

# How to run a Python file: Mario.exe has no python.exe beside it, so it runs
# scripts itself (`Mario.exe python script.py`, see packaging/mario_exe.py).
PYTHON = [sys.executable, "python"] if FROZEN else [sys.executable]


def python_ps() -> str:
    """PYTHON as a PowerShell call target, for prompts: `& <this> script.py`."""
    return " ".join([f'"{PYTHON[0]}"', *PYTHON[1:]])


def _default_claude_path() -> str:
    # The npm shim (claude.cmd) goes through cmd.exe, which would re-parse the
    # task text; the bundled claude.exe takes argv as-is.
    exe = (
        Path(os.environ.get("APPDATA", ""))
        / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    )
    return str(exe) if exe.exists() else "claude"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = Field(default="", repr=False)

    # How Mario addresses the user; empty leaves the name out of the prompt.
    user_name: str = ""

    # Checked against https://aistudio.google.com/docs/live-api/tools (Nov 2026):
    # gemini-3.8-live supports both blocking and non-blocking function calls.
    live_model: str = "gemini-3.8-live"
    voice: str = "Puck"
    max_session_minutes: int = 15
    # Gemini's built-in Google Search tool. Off by default: on a free-tier key
    # it has no quota and the Live session closes with 1011 "exceeded quota".
    enable_google_search: bool = False

    # Required for every request that doesn't come straight from this PC
    # (the phone, or anything through `tailscale serve`). Without it, only
    # local requests are served.
    access_code: str = Field(default="", repr=False)

    token_rate_limit: int = 10
    tool_rate_limit: int = 60

    # Folder of shortcuts (.lnk/.url/.bat/.exe) that open_program searches
    # first; the Start Menu is searched after it.
    programs_dir: Path = USER_DIR / "programs"
    screenshot_script: Path = RESOURCE_DIR / "backend" / "scripts" / "screenshot.ps1"

    claude_path: str = Field(default_factory=_default_claude_path)
    claude_workdir: Path = USER_DIR / "claude-workspace"
    claude_timeout_minutes: int = 15

    # Skills Claude Code taught Mario (see app/tools/skills.py).
    skills_dir: Path = USER_DIR / "skills"

    data_dir: Path = STATE_DIR / "data"

    # Long-term memory (app/memory): notes said by voice, documents from this
    # folder (pdf/md/txt) and, on request, recent Gmail messages.
    memory_docs_dir: Path = USER_DIR / "notes"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    # Text model of the specialist agents (app/agents). Lite: a spoken turn waits
    # for it (~1 s per call against ~3 s for gemini-3.5-flash, Nov 2026).
    agent_model: str = "gemini-3.5-flash-lite"
    # LLM-as-judge in evals/run_eval.py; a different model than the agent's, so
    # it does not grade its own answers. gemini-3.5-flash would be the stronger
    # judge, but its free tier allows 20 requests a day (Nov 2026).
    judge_model: str = "gemini-3.1-flash-lite"
    memory_top_k: int = 5
    chunk_size: int = 400
    chunk_overlap: int = 80

    # OAuth client of type "Desktop app" from Google Cloud Console; see README.
    gmail_client_file: Path = STATE_DIR / "gmail-client.json"

    # Things Mario says first (app/scheduler.py): due reminders always; new
    # primary-inbox mail and calendar events about to start when Google is
    # connected; a low battery below this percent (0 turns it off).
    notify_mail: bool = True
    notify_calendar: bool = True
    notify_battery_percent: int = 20
    # Also show them as Windows notifications, for when Mario's page is closed.
    toast_notifications: bool = True
    # The background loop; tests turn it off.
    scheduler_enabled: bool = True

    # Wake-on-LAN targets for wake_device, as JSON: name -> MAC, optionally
    # "MAC@broadcast" when the device is on another subnet's broadcast address.
    wol_devices: dict[str, str] = {}
    wol_broadcast: str = "255.255.255.255"
    wol_port: int = 9

    # A pending destructive action must be confirmed by voice within this time.
    confirm_ttl_seconds: int = 90

    # When true, destructive tools report what they would do instead of doing
    # it. Used for demos and end-to-end tests.
    dry_run: bool = False

    @property
    def audit_log(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def memory_db(self) -> Path:
        return self.data_dir / "memory.sqlite"

    @property
    def gmail_token_file(self) -> Path:
        return self.data_dir / "gmail-token.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
