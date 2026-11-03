import hmac
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request

from .config import Settings, get_settings
from .confirm import PendingActions
from .gemini import GeminiService
from .tools import Context
from .tools.claude import ClaudeJobs

LOOPBACK = {"127.0.0.1", "::1"}
# `tailscale serve` connects from loopback too, but adds these headers.
PROXY_HEADERS = ("x-forwarded-for", "tailscale-user-login", "forwarded")

_service: GeminiService | None = None


def get_gemini(settings: Settings = Depends(get_settings)) -> GeminiService:
    global _service
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="Sunucuda GEMINI_API_KEY yok.")
    if _service is None:
        _service = GeminiService(settings)
    return _service


@lru_cache
def get_pending() -> PendingActions:
    return PendingActions(get_settings().confirm_ttl_seconds)


@lru_cache
def get_context() -> Context:
    s = get_settings()
    jobs = ClaudeJobs(s.claude_path, s.claude_workdir, s.claude_timeout_minutes, s.audit_log, s.dry_run)
    return Context(settings=s, jobs=jobs)


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK and not any(h in request.headers for h in PROXY_HEADERS)


def require_access(
    request: Request, x_access_code: str = Header(default=""), settings: Settings = Depends(get_settings)
) -> None:
    """This API runs commands on the PC: anything not from the PC itself needs the code."""
    if is_local(request):
        return
    if not settings.access_code:
        raise HTTPException(status_code=403, detail="Uzaktan erişim kapalı. backend/.env içinde ACCESS_CODE ayarla.")
    if not hmac.compare_digest(x_access_code.encode(), settings.access_code.encode()):
        raise HTTPException(status_code=401, detail="Erişim kodu yanlış ya da eksik.")
