"""Google Calendar, read-only, through the same sign-in as Gmail
(scripts/gmail_login.py asks for both). Event titles and descriptions can come
from other people's invitations, so results are marked untrusted_content."""

import asyncio
import datetime as dt

import httpx

from ..config import Settings
from . import gmail
from .web import DAYS

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MAX_EVENTS = 15

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


def _start(event: dict) -> tuple[dt.datetime, bool]:
    """Local start time and whether it's an all-day event."""
    start = event.get("start", {})
    if "dateTime" in start:
        return dt.datetime.fromisoformat(start["dateTime"]).astimezone().replace(tzinfo=None), False
    return dt.datetime.fromisoformat(start.get("date", "1970-01-01")), True


def fetch(settings: Settings, start: dt.datetime, end: dt.datetime) -> list[dict] | dict:
    """Events between two local times, or an error result."""
    try:
        token = "test-token" if transport else gmail._token(settings)
        with httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=20, transport=transport
        ) as client:
            res = client.get(
                API,
                params={
                    "timeMin": start.astimezone().isoformat(),
                    "timeMax": end.astimezone().isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": MAX_EVENTS,
                },
            )
            res.raise_for_status()
    except gmail.NotConnected:
        return {
            "status": "error",
            "message": "Google hesabı bağlı değil. Bilgisayarda scripts\\gmail_login.py bir kez çalıştırılmalı.",
        }
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            return {
                "status": "error",
                "message": "Takvim izni yok. scripts\\gmail_login.py'yi yeniden çalıştırıp takvim iznini ver.",
            }
        return {
            "status": "error",
            "message": f"Takvim isteği başarısız oldu ({exc.response.status_code}).",
        }
    except httpx.HTTPError as exc:
        return {"status": "error", "message": f"Takvime ulaşılamadı ({exc.__class__.__name__})."}
    out = []
    for e in res.json().get("items", []):
        if e.get("status") == "cancelled":
            continue
        when, all_day = _start(e)
        out.append(
            {
                "id": e.get("id", ""),
                "title": e.get("summary", "(başlıksız)"),
                "start": when.isoformat(timespec="minutes"),
                "all_day": all_day,
                "location": e.get("location", ""),
            }
        )
    return out


def _say(event: dict, today: dt.date) -> str:
    when = dt.datetime.fromisoformat(event["start"])
    day = "" if when.date() == today else f"{DAYS[when.weekday()]} "
    time = "gün boyu" if event["all_day"] else f"{when:%H:%M}"
    return f"{day}{time} {event['title']}"


async def list_events(args: dict, settings: Settings, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    try:
        days = max(1, min(14, int(args.get("days") or 1)))
    except (TypeError, ValueError):
        days = 1
    # "Today" starts at midnight, so this morning's events still count.
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=days)
    events = await asyncio.to_thread(fetch, settings, start, end)
    if isinstance(events, dict):
        return events
    span = "Bugün" if days == 1 else f"Önümüzdeki {days} günde"
    if not events:
        return {"status": "ok", "message": f"{span} takviminde etkinlik yok.", "events": []}
    lines = "; ".join(_say(e, now.date()) for e in events)
    return {
        "status": "ok",
        "message": f"{span} {len(events)} etkinlik: {lines}.",
        "untrusted_content": True,
        "events": events,
    }
