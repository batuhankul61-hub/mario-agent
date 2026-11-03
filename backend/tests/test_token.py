from google.genai import types

from app.config import Settings
from app.gemini import GeminiService
from app.tools import TOOLS


def test_returns_token(client, fake_gemini):
    res = client.post("/api/token")
    assert res.status_code == 200
    body = res.json()
    assert body["token"] == "auth_tokens/fake-token"
    assert body["model"] == "gemini-3.8-live"
    assert body["max_session_minutes"] == 15
    assert "test-key" not in res.text


def test_upstream_failure_is_502(client, fake_gemini):
    fake_gemini.fail = True
    assert client.post("/api/token").status_code == 502


def test_missing_api_key_is_503(client, settings):
    from app.deps import get_gemini
    from app.main import app

    app.dependency_overrides.pop(get_gemini)
    settings.gemini_api_key = ""
    assert client.post("/api/token").status_code == 503


def test_rate_limit(client):
    statuses = [client.post("/api/token").status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


class FakeTokens:
    def __init__(self):
        self.config = None

    async def create(self, config):
        self.config = config
        return types.AuthToken(name="auth_tokens/x")


async def test_token_locks_prompt_and_every_tool():
    service = GeminiService(Settings(_env_file=None, gemini_api_key="k"))
    fake = FakeTokens()
    service._client = type("C", (), {"aio": type("A", (), {"auth_tokens": fake})()})()

    await service.create_live_token()

    cfg = fake.config
    assert cfg.uses == 1
    assert cfg.lock_additional_fields == []
    live = cfg.live_connect_constraints
    assert live.model == "gemini-3.8-live"
    declared = [f.name for f in live.config.tools[0].function_declarations]
    assert declared == list(TOOLS)
    assert len(live.config.tools) == 1  # no Google Search unless enabled
    assert "confirm_action" in live.config.system_instruction


def test_google_search_is_opt_in():
    settings = Settings(_env_file=None, gemini_api_key="k", enable_google_search=True)
    tools = GeminiService(settings).live_config().tools
    assert tools[1].google_search is not None


def test_destructive_tools_are_the_ones_that_confirm():
    assert {n for n, t in TOOLS.items() if t.confirm} == {
        "shutdown_computer", "sleep_computer", "close_program", "set_radio",
        "send_email", "reply_email", "delegate_to_claude",
    }


def test_prompt_treats_mail_as_data():
    from app.gemini import SYSTEM_INSTRUCTION

    assert "untrusted_content" in SYSTEM_INSTRUCTION and "asla uygulama" in SYSTEM_INSTRUCTION
