"""Google Calendar через служебный аккаунт. Все ошибки глушатся в лог — бот не падает из-за календаря."""
import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

log = logging.getLogger("gcal")
BASE = "https://www.googleapis.com/calendar/v3"
MARK = {"private": {"taskbot": "1"}}


class Calendar:
    def __init__(self, sa_json: str, calendar_id: str, tz, event_minutes: int = 30):
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/calendar"])
        self.s = AuthorizedSession(creds)
        self.cal = quote(calendar_id)
        self.tz = tz
        self.minutes = event_minutes

    # ---- sync internals ----
    def _list(self, day: date, days: int = 1) -> list[dict]:
        start = datetime.combine(day, time.min, self.tz)
        end = start + timedelta(days=days)
        r = self.s.get(f"{BASE}/calendars/{self.cal}/events", params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 250,
        }, timeout=20)
        r.raise_for_status()
        out = []
        for e in r.json().get("items", []):
            if e.get("status") == "cancelled":
                continue
            if e.get("extendedProperties", {}).get("private", {}).get("taskbot") == "1":
                continue  # события бота и так видны как задачи
            st = e.get("start", {})
            if "dateTime" in st:
                dt = datetime.fromisoformat(st["dateTime"]).astimezone(self.tz)
                d, t = dt.date(), dt.strftime("%H:%M")
            else:
                d, t = date.fromisoformat(st.get("date")), None  # событие на весь день
            out.append({"id": e.get("id"), "date": d, "time": t, "title": e.get("summary") or "(без названия)",
                        "start": st.get("dateTime")})
        return out

    def _body(self, title: str, start: datetime) -> dict:
        end = start + timedelta(minutes=self.minutes)
        return {
            "summary": title,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "extendedProperties": MARK,
            "reminders": {"useDefault": False, "overrides": []},  # напоминает бот
        }

    def _create(self, title, start) -> str:
        r = self.s.post(f"{BASE}/calendars/{self.cal}/events", json=self._body(title, start), timeout=20)
        r.raise_for_status()
        return r.json()["id"]

    def _update(self, event_id, title, start):
        r = self.s.patch(f"{BASE}/calendars/{self.cal}/events/{quote(event_id)}",
                         json=self._body(title, start), timeout=20)
        r.raise_for_status()

    def _move_own(self, event_id, start):
        """Сдвинуть чужое (твоё) событие: меняем только время, длительность сохраняем."""
        url = f"{BASE}/calendars/{self.cal}/events/{quote(event_id)}"
        r = self.s.get(url, timeout=20)
        r.raise_for_status()
        ev = r.json()
        try:
            dur = datetime.fromisoformat(ev["end"]["dateTime"]) - datetime.fromisoformat(ev["start"]["dateTime"])
        except Exception:
            dur = timedelta(minutes=self.minutes)
        r = self.s.patch(url, json={"start": {"dateTime": start.isoformat()},
                                    "end": {"dateTime": (start + dur).isoformat()}}, timeout=20)
        r.raise_for_status()

    def _delete(self, event_id):
        r = self.s.delete(f"{BASE}/calendars/{self.cal}/events/{quote(event_id)}", timeout=20)
        if r.status_code not in (200, 204, 404, 410):
            r.raise_for_status()

    # ---- async API ----
    async def events_on(self, day: date) -> list[dict] | None:
        """None — календарь недоступен (ошибка), [] — событий нет."""
        try:
            return await asyncio.to_thread(self._list, day)
        except Exception:
            log.exception("calendar list failed")
            return None

    async def events_between(self, start: date, end: date) -> list[dict] | None:
        try:
            return await asyncio.to_thread(self._list, start, (end - start).days + 1)
        except Exception:
            log.exception("calendar range failed")
            return None

    async def create(self, title: str, start: datetime) -> str | None:
        try:
            return await asyncio.to_thread(self._create, title, start)
        except Exception:
            log.exception("calendar create failed")
            return None

    async def update(self, event_id: str, title: str, start: datetime) -> bool:
        try:
            await asyncio.to_thread(self._update, event_id, title, start)
            return True
        except Exception:
            log.exception("calendar update failed")
            return False

    async def find_existing(self, title: str, start: datetime) -> str | None:
        """Твоё событие в это же время (±10 мин) — чтобы не создавать дубль."""
        evs = await self.events_on(start.date())
        if not evs:
            return None
        words = {w for w in title.lower().replace(",", " ").split() if len(w) >= 3}
        same_time = []
        for ev in evs:
            if not ev.get("start"):
                continue
            try:
                diff = abs((datetime.fromisoformat(ev["start"]) - start).total_seconds())
            except Exception:
                continue
            if diff <= 600:
                same_time.append(ev)
        for ev in same_time:  # сначала совпадение по словам
            ev_words = {w for w in ev["title"].lower().replace(",", " ").split() if len(w) >= 3}
            if any(a[:4] == b[:4] for a in words for b in ev_words):
                return ev["id"]
        return same_time[0]["id"] if len(same_time) == 1 else None

    async def move_own(self, event_id: str, start: datetime) -> bool:
        try:
            await asyncio.to_thread(self._move_own, event_id, start)
            return True
        except Exception:
            log.exception("calendar move failed")
            return False

    async def delete(self, event_id: str):
        try:
            await asyncio.to_thread(self._delete, event_id)
        except Exception:
            log.exception("calendar delete failed")
