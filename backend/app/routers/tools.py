"""Runs the function calls the Live model makes. The browser relays each
`toolCall` here and sends the result back to Gemini as a `toolResponse`."""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import audit
from ..config import get_settings
from ..confirm import PendingActions, is_affirmative
from ..deps import get_context, get_pending, require_access
from ..ratelimit import RateLimiter, enforce
from ..tools import TOOLS, Context, Result
from ..tools import system

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tools"], dependencies=[Depends(require_access)])
limiter = RateLimiter(get_settings().tool_rate_limit)


class ToolCall(BaseModel):
    name: str
    args: dict = Field(default_factory=dict)
    # What the user said (input transcription) since the last confirmation
    # question. Only used by confirm_action.
    utterances: list[str] = Field(default_factory=list)


class ButtonAnswer(BaseModel):
    approved: bool


async def execute(name: str, args: dict, ctx: Context, confirmed_by: str | None = None) -> Result:
    tool = TOOLS[name]
    try:
        result = await tool.run(args, ctx)  # type: ignore[misc]  # meta tools never reach here
    except Exception as exc:
        log.exception("Tool %s failed", name)
        result = {"status": "error", "message": f"Beklenmedik bir hata oldu ({exc.__class__.__name__})."}
    audit.record(ctx.settings.audit_log, tool=name, args=args, status=result.get("status"),
                 message=result.get("message"), confirmed_by=confirmed_by, dry_run=ctx.settings.dry_run)
    return result


async def confirm(args: dict, utterances: list[str], ctx: Context, pending: PendingActions) -> Result:
    item = pending.get(str(args.get("confirmation_id", "")))
    if item is None:
        return {"status": "error", "message": "Bekleyen böyle bir iş yok ya da onay süresi doldu. İsteği yeniden al."}
    if not is_affirmative(utterances):
        audit.record(ctx.settings.audit_log, tool=item.tool, args=item.args, status="not_confirmed",
                     heard=utterances)
        return {
            "status": "not_confirmed",
            "message": "Kullanıcının sesli onayı duyulmadı; iş yapılmadı. Soruyu bir kez daha sor.",
            "heard": utterances,
        }
    pending.pop(item.id)
    return await execute(item.tool, item.args, ctx, confirmed_by="voice: " + " / ".join(utterances))


@router.post("/tool")
async def run_tool(
    call: ToolCall,
    request: Request,
    ctx: Context = Depends(get_context),
    pending: PendingActions = Depends(get_pending),
) -> Result:
    enforce(limiter, request)
    tool = TOOLS.get(call.name)
    if tool is None:
        return {"status": "error", "message": f"Bilinmeyen araç: {call.name}."}

    if call.name == "confirm_action":
        return await confirm(call.args, call.utterances, ctx, pending)
    if call.name == "cancel_action":
        item = pending.pop(str(call.args.get("confirmation_id", "")))
        if item:
            audit.record(ctx.settings.audit_log, tool=item.tool, args=item.args, status="cancelled")
        return {"status": "ok", "message": "İş iptal edildi." if item else "İptal edilecek bekleyen iş yoktu."}

    if tool.confirm:
        item = pending.add(call.name, call.args, tool.confirm(call.args))
        audit.record(ctx.settings.audit_log, tool=call.name, args=call.args, status="needs_confirmation",
                     confirmation_id=item.id)
        return {
            "status": "needs_confirmation",
            "confirmation_id": item.id,
            "question": item.question,
            "message": "Henüz bir şey yapılmadı. Kullanıcıya soruyu sor ve cevabını bekle.",
        }
    return await execute(call.name, call.args, ctx)


@router.post("/confirm/{confirmation_id}")
async def answer_by_button(
    confirmation_id: str,
    body: ButtonAnswer,
    ctx: Context = Depends(get_context),
    pending: PendingActions = Depends(get_pending),
) -> Result:
    """The Onayla / İptal buttons on the screen: a direct user action, no model involved."""
    item = pending.pop(confirmation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Bekleyen böyle bir iş yok ya da süresi doldu.")
    if not body.approved:
        audit.record(ctx.settings.audit_log, tool=item.tool, args=item.args, status="cancelled", by="button")
        return {"status": "ok", "message": "İş iptal edildi.", "tool": item.tool}
    result = await execute(item.tool, item.args, ctx, confirmed_by="button")
    return {**result, "tool": item.tool}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, ctx: Context = Depends(get_context)) -> Result:
    if job_id not in ctx.jobs.jobs:
        raise HTTPException(status_code=404, detail="Böyle bir görev yok.")
    return await ctx.jobs.status(job_id)


@router.get("/audit")
async def audit_log(ctx: Context = Depends(get_context)) -> list[dict]:
    return audit.tail(ctx.settings.audit_log)


@router.get("/screenshots/{name}")
async def screenshot(name: str) -> FileResponse:
    # Only files the screenshot tool writes: no paths, only its own naming.
    if not re.fullmatch(r"Ekran görüntüsü [\d\- ()]+\.png", name):
        raise HTTPException(status_code=404)
    path = system.screenshot_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/png")
