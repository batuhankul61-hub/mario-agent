"""Runtime settings, read from environment variables or backend/.env."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
ENV_FILE = BACKEND_DIR / ".env"


def _default_claude_path() -> str:
    # The npm shim (claude.cmd) goes through cmd.exe, which would re-parse the
    # task text; the bundled claude.exe takes argv as-is.
    exe = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    return str(exe) if exe.exists() else "claude"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = Field(default="", repr=False)

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
    programs_dir: Path = ROOT_DIR / "programs"
    screenshot_script: Path = BACKEND_DIR / "scripts" / "screenshot.ps1"

    claude_path: str = Field(default_factory=_default_claude_path)
    claude_workdir: Path = ROOT_DIR / "claude-workspace"
    claude_timeout_minutes: int = 15

    data_dir: Path = BACKEND_DIR / "data"

    # A pending shutdown/Claude task must be confirmed by voice within this time.
    confirm_ttl_seconds: int = 90

    # When true, destructive tools report what they would do instead of doing
    # it. Used for demos and end-to-end tests.
    dry_run: bool = False

    @property
    def audit_log(self) -> Path:
        return self.data_dir / "audit.jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
