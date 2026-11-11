import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..deps import get_gemini, require_access
from ..gemini import GeminiService
from ..ratelimit import RateLimiter, enforce

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["live"])
limiter = RateLimiter(get_settings().token_rate_limit)


class TokenRequest(BaseModel):
    # Handle from the server's last sessionResumptionUpdate: the new connection
    # continues the same conversation. It only selects history; the prompt and
    # tools stay locked by this server.
    resume_handle: str | None = Field(default=None, max_length=2048, pattern=r"^[\w\-./=+]+$")


class TokenResponse(BaseModel):
    token: str
    model: str
    expires_at: str
    max_session_minutes: int


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(require_access)])
async def create_token(
    request: Request,
    body: TokenRequest | None = None,
    gemini: GeminiService = Depends(get_gemini),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Give the browser a single-use, short-lived token with Mario's prompt and tools locked in."""
    enforce(limiter, request)
    handle = body.resume_handle if body else None
    try:
        token = await gemini.create_live_token(resume_handle=handle)
    except Exception:
        log.exception("Creating a Live token failed")
        raise HTTPException(status_code=502, detail="Ses oturumu için token alınamadı.")

    return TokenResponse(
        token=token.name or "",
        model=settings.live_model,
        expires_at=token.expire_time or "",
        max_session_minutes=settings.max_session_minutes,
    )
