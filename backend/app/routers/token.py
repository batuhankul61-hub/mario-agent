import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..deps import get_gemini, require_access
from ..gemini import GeminiService
from ..ratelimit import RateLimiter, enforce

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["live"])
limiter = RateLimiter(get_settings().token_rate_limit)


class TokenResponse(BaseModel):
    token: str
    model: str
    expires_at: str
    max_session_minutes: int


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(require_access)])
async def create_token(
    request: Request,
    gemini: GeminiService = Depends(get_gemini),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Give the browser a single-use, short-lived token with Mario's prompt and tools locked in."""
    enforce(limiter, request)
    try:
        token = await gemini.create_live_token()
    except Exception:
        log.exception("Creating a Live token failed")
        raise HTTPException(status_code=502, detail="Ses oturumu için token alınamadı.")

    return TokenResponse(
        token=token.name or "",
        model=settings.live_model,
        expires_at=token.expire_time or "",
        max_session_minutes=settings.max_session_minutes,
    )
