"""Вся логика бота без привязки к Telegram: на выходе — список (текст, клавиатура)."""
import html
import re
from datetime import date, datetime, time, timedelta

from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as KB

WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
Out = list[tuple[str, KB | None]]


def e(s) -> str:
    return html.escape(str(s))


def parse_hhmm(s: str) -> time | None:
    try:
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        return None


def parse_date(s) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except Exception:
        return None


class Core:
    def __init__(self, db, tz, default_areas, default_morning, default_evening, parser=None, cal=None):
        self.db, self.tz, self.parser, self.cal = db, tz, parser, cal
        self.agent = None  # подключается в bot.py
        self.crm = None    # подключается в bot.py
        self.default_areas = default_areas
        self.default_morning, self.default_evening = default_morning, default_evening
        self.agent_history: list[tuple[str, str]] = []

    def now(self) -> datetime:
        return datetime.now(self.tz)

    # ---------- форматирование ----------
    def fmt_day(self, d: date) -> str:
        today = self.now().date()
        if d == today:
            return "сегодня"
        if d == today + timedelta(days=1):
            return "завтра"
        if d == today - timedelta(days=1):
            return "вчера"
        return f"{WD[d.weekday()]} {d:%d.%m}"

    def when(self, t) -> str:
        parts = []
        if t["due_date"]:
            parts.append(self.fmt_day(t["due_date"]))
        if t["remind_at"]:
            parts.append(t["remind_at"].astimezone(self.tz).strftime("%H:%M"))
        return " ".join(parts)

    def line(self, t, show_when=True, show_area=False) -> str:
        s = f"• {e(t['title'])}"
        extra = []
        if show_area and t["area"]:
            extra.append(e(t["area"]))
        if show_when and (w := self.when(t)):
            extra.append(w)
        if extra:
            s += f" <i>— {' · '.join(extra)}</i>"
        return s + f"  /d{t['id']}"

    def card(self, t) -> str:
        bits = []
        if t["area"]:
            bits.append(f"🏷 {e(t['area'])}")
        if t["due_date"]:
            bits.append(f"📅 {self.fmt_day(t['due_date'])}")
        if t["remind_at"]:
            bits.append(f"⏰ {t['remind_at'].astimezone(self.tz):%H:%M}")
        if t["gcal_event_id"]:
            bits.append("🗓 в календаре")
        return f"📌 <b>{e(t['title'])}</b>\n" + (" · ".join(bits) if bits else "без срока")

    # ---------- клавиатуры ----------
    @staticmethod
    def kb_new(tid):
        return KB(inline_keyboard=[[B(text="👌 Ок", callback_data=f"ok:{tid}"),
                                    B(text="✏️ Поправить", callback_data=f"ed:{tid}"),
                                    B(text="🗑", callback_data=f"del:{tid}")]])

    @staticmethod
    def kb_remind(tid):
        return KB(inline_keyboard=[
            [B(text="✅ Сделано", callback_data=f"d:{tid}")],
            [B(text="+1 час", callback_data=f"s:{tid}:1h"),
             B(text="Вечером", callback_data=f"s:{tid}:eve"),
             B(text="Завтра", callback_data=f"m:{tid}:1")]])

    @staticmethod
    def kb_review(tid):
        return KB(inline_keyboard=[
            [B(text="✅ Сделано", callback_data=f"d:{tid}")],
            [B(text="Завтра", callback_data=f"m:{tid}:1"),
             B(text="+3 дня", callback_data=f"m:{tid}:3"),
             B(text="Пн", callback_data=f"m:{tid}:mon"),
             B(text="Без срока", callback_data=f"m:{tid}:none")]])

    # ---------- настройки ----------
    async def areas(self) -> list[str]:
        return await self.db.get_areas(self.default_areas)

    async def morning_time(self) -> time:
        return parse_hhmm(await self.db.get("morning_time", self.default_morning))

    async def evening_time(self) -> time:
        return parse_hhmm(await self.db.get("evening_time", self.default_evening))

    async def match_area(self, name: str | None) -> str | None:
        if not name:
            return None
        for a in await self.areas():
            if a.lower() == name.strip().lower():
                return a
        return None

    # ---------- календарь ----------
    async def sync_event(self, t):
        """Привести событие в календаре в соответствие с задачей."""
        if not self.cal:
            return t
        has_time = t["status"] == "open" and t["remind_at"] is not None
        ev = t["gcal_event_id"]
        if has_time:
            start = t["remind_at"].astimezone(self.tz)
            if ev:
                await self.cal.update(ev, t["title"], start)
            else:
                ev = await self.cal.create(t["title"], start)
                if ev:
                    t = await self.db.update_task(t["id"], gcal_event_id=ev)
        elif ev and t["status"] == "open":
            await self.cal.delete(ev)
            t = await self.db.update_task(t["id"], gcal_event_id=None)
        return t

    # ---------- операции с задачами ----------
    def _remind_at(self, d: date | None, tm: time | None) -> datetime | None:
        if not tm:
            return None
        return datetime.combine(d or self.now().date(), tm, self.tz)

    async def add(self, title, area, due: date | None, tm: time | None):
        if tm and not due:
            due = self.now().date()
        t = await self.db.add_task(title.strip(), area, due, self._remind_at(due, tm))
        return await self.sync_event(t)

    async def complete(self, tid: int) -> str:
        t = await self.db.get_task(tid)
        if not t:
            return "Такой задачи нет."
        if t["status"] == "done":
            return f"Уже закрыта: {e(t['title'])}"
        await self.db.update_task(tid, status="done", done_at=self.now())
        return f"✅ Готово: <s>{e(t['title'])}</s>"

    async def delete(self, tid: int) -> str:
        t = await self.db.get_task(tid)
        if not t:
            return "Такой задачи нет."
        if self.cal and t["gcal_event_id"]:
            await self.cal.delete(t["gcal_event_id"])
        await self.db.delete_task(tid)
        return f"🗑 Удалил: <s>{e(t['title'])}</s>"

    async def move(self, tid: int, mode: str) -> str:
        """Перенос срока (с разбора / «завтра» из напоминания)."""
        t = await self.db.get_task(tid)
        if not t:
            return "Такой задачи нет."
        today = self.now().date()
        if mode == "none":
            t = await self.db.update_task(tid, due_date=None, remind_at=None, snooze_at=None, reminded=False)
        else:
            if mode == "mon":
                new = today + timedelta(days=(7 - today.weekday()) or 7)
            else:
                new = today + timedelta(days=int(mode))
            tm = t["remind_at"].astimezone(self.tz).time() if t["remind_at"] else None
            t = await self.db.update_task(tid, due_date=new, remind_at=self._remind_at(new, tm),
                                          snooze_at=None, reminded=False)
        t = await self.sync_event(t)
        return f"➡️ {e(t['title'])} — {self.when(t) or 'без срока'}"

    async def snooze(self, tid: int, mode: str) -> str:
        """Отложить только напоминание, срок и событие не трогаем."""
        t = await self.db.get_task(tid)
        if not t:
            return "Такой задачи нет."
        now = self.now()
        if mode == "1h":
            at = now + timedelta(hours=1)
        else:  # eve
            at = datetime.combine(now.date(), await self.evening_time(), self.tz)
            if at <= now:
                at = now + timedelta(hours=1)
        await self.db.update_task(tid, snooze_at=at, reminded=False)
        return f"⏰ Напомню в {at:%H:%M}: {e(t['title'])}"

    async def edit(self, tid: int, a: dict) -> Out:
        t = await self.db.get_task(tid)
        if not t:
            return [("Такой задачи нет.", None)]
        f = {}
        if a.get("title"):
            f["title"] = a["title"].strip()
        if a.get("area") and (area := await self.match_area(a["area"])):
            f["area"] = area
        due = t["due_date"]
        tm = t["remind_at"].astimezone(self.tz).time() if t["remind_at"] else None
        if a.get("due_date") == "none":
            due, tm = None, None
        elif d := parse_date(a.get("due_date")):
            due = d
        if a.get("time") == "none":
            tm = None
        elif x := parse_hhmm(a.get("time") or ""):
            tm = x
        if tm and not due:
            due = self.now().date()
        f["due_date"] = due
        new_at = self._remind_at(due, tm)
        if new_at != t["remind_at"]:
            f["remind_at"], f["reminded"], f["snooze_at"] = new_at, False, None
        t = await self.db.update_task(tid, **f)
        t = await self.sync_event(t)
        return [("✏️ Обновил\n" + self.card(t), self.kb_new(t["id"]))]

    # ---------- свободный текст ----------
    async def handle_text(self, text: str, edit_task_id: int | None = None, notify=None) -> Out:
        now = self.now()
        tasks = await self.db.open_tasks()
        if edit_task_id:
            t = await self.db.get_task(edit_task_id)
            if t:
                text = (f"Поправка к задаче #{t['id']} «{t['title']}» "
                        f"(сфера: {t['area'] or '-'}, срок: {self.when(t) or 'без срока'}). "
                        f"Используй edit с task_id={t['id']}. Поправка: {text}")
        rows = await self.db.recent_dialog(now - timedelta(hours=3), 8)
        recent = [f"[{r['ts'].astimezone(self.tz):%H:%M}] «{r['user_text'][:600]}» → {r['result'][:500]}" for r in rows]
        # прошлые успешные поручения — как контекст для агента
        self.agent_history = [(r["instruction"], r["result"]) for r in rows if r["ok"] and r["instruction"]][-4:]
        self.last_instruction = None
        try:
            res = await self.parser.parse(text, now, await self.areas(), tasks, recent)
            out = await self.apply(res, notify)
        except Exception as ex:
            await self.db.add_dialog(text, f"ОШИБКА — не выполнено ({type(ex).__name__})", False,
                                     getattr(self, "last_instruction", None))
            raise
        done = "\n".join(html.unescape(re.sub(r"<[^>]+>", "", t)) for t, _ in out)[:3000]
        await self.db.add_dialog(text, done, True, getattr(self, "last_instruction", None))
        return out

    async def apply(self, res: dict, notify=None) -> Out:
        out: Out = []
        for a in res.get("actions") or []:
            typ = a.get("type")
            tid = a.get("task_id")
            if typ == "add" and a.get("title"):
                t = await self.add(a["title"], await self.match_area(a.get("area")),
                                   parse_date(a.get("due_date")), parse_hhmm(a.get("time") or ""))
                out.append((self.card(t), self.kb_new(t["id"])))
            elif typ == "complete" and tid:
                out.append((await self.complete(tid), None))
            elif typ == "delete" and tid:
                out.append((await self.delete(tid), None))
            elif typ == "edit" and tid:
                a = {k: v for k, v in a.items() if k in ("title", "area", "due_date", "time")}
                out += await self.edit(tid, a)
            elif typ == "delegate" and a.get("instruction"):
                if not self.agent:
                    out.append(("Режим поручений не подключён.", None))
                    continue
                self.last_instruction = a["instruction"]
                if notify:
                    await notify("🔎 Принял, работаю…")
                out.append((await self.agent.run(a["instruction"], history=self.agent_history), "agent"))
            elif typ == "list":
                out.append((await self.list_text(a.get("period") or "all", a.get("area")), None))
        if not out:
            out.append((e(res.get("reply") or "Не понял, переформулируй, пожалуйста."), None))
        return out

    # ---------- списки ----------
    async def list_text(self, period: str = "all", area: str | None = None) -> str:
        today = self.now().date()
        area = await self.match_area(area) if area else None
        tasks = await self.db.open_tasks()
        if area:
            tasks = [t for t in tasks if t["area"] == area]
        titles = {"today": "Сегодня", "tomorrow": "Завтра", "week": "Ближайшие 7 дней",
                  "overdue": "Просрочено", "all": "Все открытые задачи"}
        if period == "today":
            tasks = [t for t in tasks if t["due_date"] and t["due_date"] <= today]
        elif period == "tomorrow":
            tasks = [t for t in tasks if t["due_date"] == today + timedelta(days=1)]
        elif period == "week":
            tasks = [t for t in tasks if t["due_date"] and t["due_date"] <= today + timedelta(days=7)]
        elif period == "overdue":
            tasks = [t for t in tasks if t["due_date"] and t["due_date"] < today]
        head = f"<b>{titles.get(period, 'Задачи')}</b>" + (f" · {e(area)}" if area else "")
        if not tasks:
            return head + "\n\nПусто 👌"
        if area:
            return head + "\n\n" + "\n".join(self.line(t) for t in tasks)
        groups: dict[str, list] = {}
        for t in tasks:
            groups.setdefault(t["area"] or "Без сферы", []).append(t)
        order = await self.areas() + ["Без сферы"]
        parts = [f"<b>{e(g)}</b>\n" + "\n".join(self.line(t) for t in groups[g])
                 for g in sorted(groups, key=lambda g: order.index(g) if g in order else 99)]
        return head + "\n\n" + "\n\n".join(parts) + "\n\n<i>Нажми /dN, чтобы закрыть задачу</i>"

    async def calendar_block(self, day: date, title: str) -> str:
        if not self.cal:
            return ""
        evs = await self.cal.events_on(day)
        if evs is None:
            return f"{title}\n<i>календарь сейчас недоступен</i>"
        if not evs:
            return ""
        return title + "\n" + "\n".join(
            f"• {ev['time'] or 'весь день'} — {e(ev['title'])}" for ev in evs)

    # ---------- сводки ----------
    async def morning(self) -> Out:
        today = self.now().date()
        tasks = await self.db.open_tasks()
        overdue = [t for t in tasks if t["due_date"] and t["due_date"] < today]
        now_ = [t for t in tasks if t["due_date"] == today]
        week = [t for t in tasks if t["due_date"] and today < t["due_date"] <= today + timedelta(days=7)]
        nodate = [t for t in tasks if not t["due_date"]]
        blocks = [f"☀️ <b>Сводка на {WD[today.weekday()]}, {today:%d.%m}</b>"]
        if cal := await self.calendar_block(today, "🗓 <b>В календаре</b>"):
            blocks.append(cal)
        if overdue:
            blocks.append("🔥 <b>Просрочено</b>\n" + "\n".join(self.line(t, show_area=True) for t in overdue))
        if now_:
            blocks.append("📌 <b>Сегодня</b>\n" + "\n".join(self.line(t, show_area=True) for t in now_))
        if week:
            blocks.append("🗓 <b>На неделе</b>\n" + "\n".join(self.line(t, show_area=True) for t in week))
        if not (overdue or now_ or week):
            blocks.append("На сегодня задач со сроком нет.")
        if nodate:
            blocks.append(f"📥 Без срока: {len(nodate)} — /list")
        if self.crm:
            try:
                crm_lines = await self.crm.morning_lines()
            except Exception:
                crm_lines = ["<i>CRM сейчас недоступна</i>"]
            if crm_lines:
                blocks.append("📋 <b>CRM</b>\n" + "\n".join(e(x) if not x.startswith("<i>") else x for x in crm_lines))
        return [("\n\n".join(blocks), None)]

    async def evening(self) -> Out:
        today = self.now().date()
        tomorrow = today + timedelta(days=1)
        tasks = await self.db.open_tasks()
        left = [t for t in tasks if t["due_date"] and t["due_date"] <= today]
        out: Out = []
        if left:
            out.append((f"🌙 <b>Разбор дня</b>\nОсталось незакрытым: {len(left)}. Отметь, что сделано, остальное перенеси.", None))
            for t in left:
                out.append((self.card(t), self.kb_review(t["id"])))
        else:
            out.append(("🌙 <b>Разбор дня</b>\nВсё на сегодня закрыто 👌", None))
        tail = []
        if cal := await self.calendar_block(tomorrow, "🗓 <b>Завтра в календаре</b>"):
            tail.append(cal)
        tm = [t for t in tasks if t["due_date"] == tomorrow]
        if tm:
            tail.append("📌 <b>Задачи на завтра</b>\n" + "\n".join(self.line(t, show_area=True) for t in tm))
        if tail:
            out.append(("\n\n".join(tail), None))
        return out

    async def reminder(self, t) -> tuple[str, KB]:
        await self.db.update_task(t["id"], reminded=True)
        return "⏰ <b>Напоминание</b>\n" + self.card(t), self.kb_remind(t["id"])

    async def tick(self) -> Out:
        """Вызывается раз в ~30 сек: напоминания и сводки."""
        now = self.now()
        out: Out = []
        for t in await self.db.due_reminders(now):
            out.append(await self.reminder(t))
        today = now.date().isoformat()
        for key, at, fn in (("morning", await self.morning_time(), self.morning),
                            ("evening", await self.evening_time(), self.evening)):
            start = datetime.combine(now.date(), at, self.tz)
            if start <= now < start + timedelta(hours=3) and await self.db.get(f"last_{key}") != today:
                await self.db.set(f"last_{key}", today)
                out += await fn()
        return out
