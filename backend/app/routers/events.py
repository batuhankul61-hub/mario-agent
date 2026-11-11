"""GET /api/events: what Mario should say without being asked (see app/events.py)."""

from fastapi import APIRouter, Depends

from ..deps import require_access
from ..events import Events, get_events

router = APIRouter(prefix="/api", tags=["events"], dependencies=[Depends(require_access)])


@router.get("/events")
async def list_events(after: int = -1, events: Events = Depends(get_events)) -> dict:
    """after=-1 (a page that just opened) returns no old events, only where to continue from."""
    items = [] if after < 0 else events.after(after)
    return {"events": items, "last": events.last}
