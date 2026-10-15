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
    def _list(self, day: date) -> list[dict]:
        start = datetime.combine(day, time.min, self.tz)
        end = start + timedelta(days=1)
        r = self.s.get(f"{BASE}/calendars/{self.cal}/events", params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": 50,
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
                t = datetime.fromisoformat(st["dateTime"]).astimezone(self.tz).strftime("%H:%M")
            else:
                t = None  # событие на весь день
            out.append({"time": t, "title": e.get("summary") or "(без названия)"})
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

    async def delete(self, event_id: str):
        try:
            await asyncio.to_thread(self._delete, event_id)
        except Exception:
            log.exception("calendar delete failed")
