import os

# No background reminders/mail checks while the app runs under TestClient.
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest
from app.config import Settings, get_settings
from app.confirm import PendingActions
from app.deps import get_context, get_gemini, get_pending
from app.main import app
from app.routers import token, tools
from app.tools import Context
from app.tools.claude import ClaudeJobs
from fastapi.testclient import TestClient
from google.genai import types

LOCAL = ("127.0.0.1", 50000)


class FakeGemini:
    def __init__(self):
        self.fail = False
        self.calls = 0
        self.resume_handle = None

    async def create_live_token(self, resume_handle=None):
        if self.fail:
            raise RuntimeError("boom")
        self.calls += 1
        self.resume_handle = resume_handle
        return types.AuthToken(name="auth_tokens/fake-token", expire_time="2026-11-03T12:00:00Z")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        gemini_api_key="test-key",
        programs_dir=tmp_path / "programs",
        data_dir=tmp_path / "data",
        claude_workdir=tmp_path / "claude",
        claude_path="claude-not-installed",
        skills_dir=tmp_path / "skills",
        memory_docs_dir=tmp_path / "notes",
    )


@pytest.fixture(autouse=True)
def project_untouched():
    """Every test must work in tmp_path: fail loudly if one writes into the real project."""
    from app.config import ROOT_DIR

    watched = [ROOT_DIR / "skills", ROOT_DIR / "backend" / "app"]
    before = {p: p.stat().st_mtime_ns for d in watched if d.exists() for p in d.rglob("*")}
    yield
    after = {p: p.stat().st_mtime_ns for d in watched if d.exists() for p in d.rglob("*")}
    changed = [str(p) for p in after if before.get(p) != after[p] and "__pycache__" not in p.parts]
    assert not changed, f"test wrote into the project: {changed}"


@pytest.fixture(autouse=True)
def no_program_lookups(monkeypatch):
    """open_program's registry / Program Files / Get-StartApps fallbacks read the real PC."""
    from app.tools import system

    for name in ("app_paths", "program_files", "start_apps"):
        monkeypatch.setattr(system, name, lambda wanted: None)


@pytest.fixture
def ctx(settings):
    jobs = ClaudeJobs(
        settings.claude_path,
        settings.claude_workdir,
        1,
        settings.audit_log,
        skills_dir=settings.skills_dir,
    )
    return Context(settings=settings, jobs=jobs)


@pytest.fixture
def pending(settings):
    return PendingActions(settings.confirm_ttl_seconds)


@pytest.fixture
def fake_gemini():
    return FakeGemini()


def _client(settings, ctx, pending, fake_gemini, address):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_gemini] = lambda: fake_gemini
    app.dependency_overrides[get_context] = lambda: ctx
    app.dependency_overrides[get_pending] = lambda: pending
    for limiter in (token.limiter, tools.limiter):
        limiter._hits.clear()
    return TestClient(app, client=address)


@pytest.fixture
def client(settings, ctx, pending, fake_gemini):
    """A browser on this PC."""
    with _client(settings, ctx, pending, fake_gemini, LOCAL) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def phone(settings, ctx, pending, fake_gemini):
    """A browser on another device (or anything behind `tailscale serve`)."""
    with _client(settings, ctx, pending, fake_gemini, ("100.64.0.7", 50000)) as c:
        yield c
    app.dependency_overrides.clear()
