"""Reminder and timer tools. The scheduler (app/scheduler.py) fires them."""

from typing import TYPE_CHECKING

from ..reminders import Reminders, parse_due, spoken

if TYPE_CHECKING:
    from . import Context


def store(ctx: "Context") -> Reminders:
    if ctx.reminders is None:
        ctx.reminders = Reminders(ctx.settings.data_dir / "reminders.json")
    return ctx.reminders


async def set_reminder(args: dict, ctx: "Context") -> dict:
    text = (args.get("text") or "").strip()
    kind = "timer" if args.get("kind") == "timer" else "reminder"
    if not text:
        text = "Zamanlayıcı doldu" if kind == "timer" else ""
    if not text:
        return {"status": "error", "message": "Neyi hatırlatayım?"}
    due = parse_due(args.get("in_minutes"), args.get("at"))
    if isinstance(due, str):
        return {"status": "error", "message": due}
    item = store(ctx).add(text, due, kind)
    what = "Zamanlayıcıyı" if kind == "timer" else f"«{text}» hatırlatmasını"
    return {
        "status": "ok",
        "message": f"{what} {spoken(due)} için kurdum.",
        "id": item.id,
        "due": item.due,
    }


async def list_reminders(args: dict, ctx: "Context") -> dict:
    items = store(ctx).pending()
    if not items:
        return {"status": "ok", "message": "Bekleyen hatırlatma yok.", "reminders": []}
    lines = "; ".join(f"{r.id}) {spoken(r.due_at)} {r.text}" for r in items[:10])
    return {
        "status": "ok",
        "message": f"{len(items)} hatırlatma: {lines}.",
        "reminders": [{"id": r.id, "text": r.text, "due": r.due, "kind": r.kind} for r in items],
    }


async def cancel_reminder(args: dict, ctx: "Context") -> dict:
    try:
        reminder_id = int(args.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {"status": "error", "message": "Hangi hatırlatma? Numarasını söyle."}
    item = store(ctx).cancel(reminder_id)
    if item is None:
        return {"status": "error", "message": f"{reminder_id} numaralı bekleyen hatırlatma yok."}
    return {"status": "ok", "message": f"«{item.text}» hatırlatmasını iptal ettim."}
