"""Background loop that lets Mario speak first: due reminders, new mail in the
primary inbox, a calendar event about to start, a low battery. Each becomes an
event (app/events.py) and, unless turned off, a Windows notification.

Checks are cheap and spaced out; anything that fails (no Gmail sign-in, no
network) is skipped quietly and tried again on the next round."""

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Callable

from . import notify
from .events import Events
from .tools import Context, gcalendar, gmail, remind

log = logging.getLogger(__name__)

TICK_SECONDS = 5
MAIL_EVERY = 300
CALENDAR_EVERY = 300
BATTERY_EVERY = 120
CALENDAR_LEAD = dt.timedelta(minutes=15)
MAIL_QUERY = "is:unread in:inbox category:primary newer_than:1d"


class Scheduler:
    def __init__(
        self,
        ctx: Context,
        events: Events,
        toast: Callable[[str, str], bool] = notify.toast,
    ):
        self.ctx = ctx
        self.events = events
        self.toast = toast
        self._next: dict[str, float] = {}
        self._seen_mail: set[str] | None = None  # None: first look, only remember
        self._seen_events: set[str] = set()
        self._battery_warned = False

    def _emit(self, kind: str, title: str, message: str, **data) -> None:
        self.events.push(kind, message, **data)
        if self.ctx.settings.toast_notifications:
            self.toast(title, message)

    def _due(self, name: str, every: float) -> bool:
        now = time.monotonic()
        if now < self._next.get(name, 0):
            return False
        self._next[name] = now + every
        return True

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Scheduler round failed")
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self, now: dt.datetime | None = None) -> None:
        self.check_reminders(now)
        s = self.ctx.settings
        if s.notify_mail and gmail.is_connected(s) and self._due("mail", MAIL_EVERY):
            await asyncio.to_thread(self.check_mail)
        if s.notify_calendar and gmail.is_connected(s) and self._due("calendar", CALENDAR_EVERY):
            await asyncio.to_thread(self.check_calendar, now)
        if s.notify_battery_percent and self._due("battery", BATTERY_EVERY):
            self.check_battery()

    def check_reminders(self, now: dt.datetime | None = None) -> None:
        for r in remind.store(self.ctx).pop_due(now):
            if r.kind == "timer":
                self._emit("reminder", "Mario · zamanlayıcı", r.text, id=r.id)
            else:
                self._emit("reminder", "Mario · hatırlatma", r.text, id=r.id)

    def check_mail(self) -> None:
        result = gmail._call(gmail._list, self.ctx.settings, MAIL_QUERY, 5)
        if result.get("status") != "ok":
            return
        mails = result.get("emails", [])
        ids = {m["id"] for m in mails}
        if self._seen_mail is None:
            self._seen_mail = ids
            return
        for m in mails:
            if m["id"] in self._seen_mail:
                continue
            self._seen_mail.add(m["id"])
            self._emit(
                "mail",
                f"Yeni e-posta · {m['from']}",
                f"{m['from']}: {m['subject']}",
                id=m["id"],
                untrusted_content=True,
            )

    def check_calendar(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now()
        events = gcalendar.fetch(self.ctx.settings, now, now + CALENDAR_LEAD)
        if isinstance(events, dict):
            return
        for e in events:
            if e["all_day"] or e["id"] in self._seen_events:
                continue
            self._seen_events.add(e["id"])
            start = dt.datetime.fromisoformat(e["start"])
            minutes = max(0, round((start - now).total_seconds() / 60))
            when = "şimdi başlıyor" if minutes == 0 else f"{minutes} dakika sonra"
            self._emit(
                "calendar",
                "Mario · takvim",
                f"{e['title']} {when} ({start:%H:%M}).",
                id=e["id"],
                untrusted_content=True,
            )

    def check_battery(self) -> None:
        import psutil  # noqa: PLC0415

        battery = psutil.sensors_battery()
        if battery is None:
            return
        low = battery.percent <= self.ctx.settings.notify_battery_percent
        if battery.power_plugged or not low:
            self._battery_warned = False
            return
        if not self._battery_warned:
            self._battery_warned = True
            self._emit(
                "battery",
                "Mario · pil",
                f"Pil yüzde {round(battery.percent)}; şarja takmak isteyebilirsin.",
            )
