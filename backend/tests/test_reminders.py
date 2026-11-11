import datetime as dt

import httpx
import pytest
from app.events import Events
from app.reminders import Reminders, parse_due, spoken
from app.scheduler import Scheduler
from app.tools import gcalendar

NOW = dt.datetime(2026, 11, 11, 20, 0, 0)


def test_parse_minutes_and_clock_times():
    assert parse_due(in_minutes=10, now=NOW) == NOW + dt.timedelta(minutes=10)
    assert parse_due(at="21:30", now=NOW) == dt.datetime(2026, 11, 11, 21, 30)
    # A time already past today means tomorrow.
    assert parse_due(at="09:00", now=NOW) == dt.datetime(2026, 11, 12, 9, 0)
    assert parse_due(at="2026-12-01T08:15", now=NOW) == dt.datetime(2026, 12, 1, 8, 15)


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"in_minutes": 0}, {"in_minutes": "çok"}, {"at": "25:00"}, {"at": "2020-01-01T10:00"}],
)
def test_parse_rejects_bad_times(kwargs):
    assert isinstance(parse_due(now=NOW, **kwargs), str)


def test_spoken_day_names():
    assert spoken(dt.datetime(2026, 11, 11, 21, 30), NOW) == "bugün 21:30"
    assert spoken(dt.datetime(2026, 11, 12, 9, 0), NOW) == "yarın 09:00"
    assert spoken(dt.datetime(2026, 12, 1, 8, 15), NOW) == "1 Aralık 08:15"


def test_store_survives_restart_and_fires_once(tmp_path):
    path = tmp_path / "r.json"
    store = Reminders(path)
    store.add("çay", NOW + dt.timedelta(minutes=5))
    store.add("kira", NOW + dt.timedelta(days=1))

    again = Reminders(path)
    assert [r.text for r in again.pending()] == ["çay", "kira"]
    assert [r.text for r in again.pop_due(NOW + dt.timedelta(minutes=6))] == ["çay"]
    assert again.pop_due(NOW + dt.timedelta(minutes=7)) == []
    assert [r.text for r in Reminders(path).pending()] == ["kira"]


def test_reminder_tools_over_api(client, ctx):
    res = client.post(
        "/api/tool", json={"name": "set_reminder", "args": {"text": "çayı al", "in_minutes": 10}}
    ).json()
    assert res["status"] == "ok" and "çayı al" in res["message"]

    listed = client.post("/api/tool", json={"name": "list_reminders"}).json()
    assert [r["text"] for r in listed["reminders"]] == ["çayı al"]

    gone = client.post("/api/tool", json={"name": "cancel_reminder", "args": {"id": res["id"]}})
    assert gone.json()["status"] == "ok"
    assert client.post("/api/tool", json={"name": "list_reminders"}).json()["reminders"] == []


def test_timer_needs_no_text(client):
    res = client.post(
        "/api/tool", json={"name": "set_reminder", "args": {"kind": "timer", "in_minutes": 5}}
    ).json()
    assert res["status"] == "ok" and res["message"].startswith("Zamanlayıcıyı")


async def test_scheduler_turns_due_reminders_into_events(ctx):
    events, toasts = Events(), []
    ctx.settings.notify_battery_percent = 0
    sched = Scheduler(ctx, events, toast=lambda t, b: toasts.append((t, b)) or True)
    await ctx_add(ctx, "ilacını iç", NOW + dt.timedelta(minutes=1))

    await sched.tick(NOW)
    assert events.after(0) == []
    await sched.tick(NOW + dt.timedelta(minutes=2))
    [event] = events.after(0)
    assert event["kind"] == "reminder" and event["message"] == "ilacını iç"
    assert toasts == [("Mario · hatırlatma", "ilacını iç")]


async def ctx_add(ctx, text, due):
    from app.tools import remind

    remind.store(ctx).add(text, due)


def test_mail_check_only_announces_new_mail(ctx, monkeypatch):
    events = Events()
    sched = Scheduler(ctx, events, toast=lambda t, b: True)
    inbox = [{"id": "a", "from": "Ali", "subject": "Selam"}]
    monkeypatch.setattr("app.tools.gmail._call", lambda *a: {"status": "ok", "emails": list(inbox)})
    sched.check_mail()  # first look: remembers what's already there
    assert events.after(0) == []
    inbox.append({"id": "b", "from": "Ayşe", "subject": "Toplantı"})
    sched.check_mail()
    sched.check_mail()
    assert [e["message"] for e in events.after(0)] == ["Ayşe: Toplantı"]
    assert events.after(0)[0]["untrusted_content"] is True


def test_events_endpoint_starts_from_now(client):
    from app.events import get_events

    events = get_events()
    events.push("reminder", "eski")
    first = client.get("/api/events").json()
    assert first["events"] == []
    events.push("reminder", "yeni")
    later = client.get(f"/api/events?after={first['last']}").json()
    assert [e["message"] for e in later["events"]] == ["yeni"]


@pytest.fixture
def calendar_api(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "1",
                        "summary": "Diş hekimi",
                        "start": {"dateTime": "2026-11-11T14:30:00+03:00"},
                    },
                    {"id": "2", "summary": "Bayram", "start": {"date": "2026-11-11"}},
                    {
                        "id": "3",
                        "status": "cancelled",
                        "summary": "x",
                        "start": {"date": "2026-11-11"},
                    },
                ]
            },
        )

    monkeypatch.setattr(gcalendar, "transport", httpx.MockTransport(handler))


async def test_calendar_lists_today(calendar_api, settings):
    res = await gcalendar.list_events({}, settings, now=NOW)
    assert res["status"] == "ok" and res["untrusted_content"]
    assert [e["title"] for e in res["events"]] == ["Diş hekimi", "Bayram"]
    assert "gün boyu Bayram" in res["message"]
