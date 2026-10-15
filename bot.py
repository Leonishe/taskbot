import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message

import config as C
from ai import Parser, transcribe
from core import Core, e, parse_hhmm
from db import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

router = Router()
core: Core
pending_edit: dict[int, int] = {}  # user_id -> task_id, ждём текст поправки

HELP = """<b>Как пользоваться</b>
Просто пиши или наговаривай голосом:
• «завтра в 15:00 созвон с Кириллом по QQ»
• «до конца месяца обновить сайт»
• «сделал отчёт для Кирилла»
• «перенеси созвон на пятницу»
• «что по EQLBM?» / «что на неделе?»

<b>Команды</b>
/today — сегодня и просрочено
/week — ближайшие 7 дней
/list — все открытые задачи
/dN — закрыть задачу №N
/settings — время сводок и сферы
/morning 12:00 — время утренней сводки
/evening 18:00 — время вечернего разбора
/areas — сферы; /area_add Название; /area_del Название"""


async def send_all(bot: Bot, chat_id: int, out):
    for text, kb in out:
        await bot.send_message(chat_id, text, reply_markup=kb)


# ---------- доступ только владельцу ----------
@router.message(CommandStart())
async def start(m: Message):
    if not C.OWNER_ID:
        await m.answer(f"Твой Telegram id: <code>{m.from_user.id}</code>\n"
                       f"Впиши его в переменную OWNER_ID на Railway и перезапусти бота.")
        return
    if m.from_user.id != C.OWNER_ID:
        return
    await m.answer("Привет, Лео. Я держу твои задачи.\n\n" + HELP)


owner = Router()
owner.message.filter(F.from_user.id == C.OWNER_ID)
owner.callback_query.filter(F.from_user.id == C.OWNER_ID)
router.include_router(owner)


@owner.message(Command("help"))
async def help_(m: Message):
    await m.answer(HELP)


@owner.message(Command("today"))
async def today(m: Message):
    await m.answer(await core.list_text("today"))


@owner.message(Command("week"))
async def week(m: Message):
    await m.answer(await core.list_text("week"))


@owner.message(Command("list"))
async def list_(m: Message):
    await m.answer(await core.list_text("all"))


@owner.message(F.text.regexp(r"^/d(\d+)(@\w+)?$"))
async def done_cmd(m: Message):
    tid = int(re.match(r"^/d(\d+)", m.text).group(1))
    await m.answer(await core.complete(tid))


@owner.message(Command("settings"))
async def settings(m: Message):
    cal = "подключён" if core.cal else "не подключён"
    voice = "включены" if C.GROQ_API_KEY else "выключены (нет GROQ_API_KEY)"
    await m.answer(
        f"<b>Настройки</b>\n"
        f"Утренняя сводка: {await core.morning_time():%H:%M} — /morning ЧЧ:ММ\n"
        f"Вечерний разбор: {await core.evening_time():%H:%M} — /evening ЧЧ:ММ\n"
        f"Сферы: {e(', '.join(await core.areas()))} — /areas\n"
        f"Календарь: {cal}\nГолосовые: {voice}")


async def _set_time(m: Message, cmd: CommandObject, key: str, label: str):
    tm = parse_hhmm(cmd.args or "")
    if not tm:
        await m.answer(f"Формат: /{cmd.command} 12:00")
        return
    await core.db.set(key, tm.strftime("%H:%M"))
    await m.answer(f"{label}: теперь в {tm:%H:%M}")


@owner.message(Command("morning"))
async def set_morning(m: Message, command: CommandObject):
    await _set_time(m, command, "morning_time", "Утренняя сводка")


@owner.message(Command("evening"))
async def set_evening(m: Message, command: CommandObject):
    await _set_time(m, command, "evening_time", "Вечерний разбор")


@owner.message(Command("areas"))
async def areas(m: Message):
    await m.answer("Сферы: " + e(", ".join(await core.areas())) +
                   "\n\n/area_add Название — добавить\n/area_del Название — убрать")


@owner.message(Command("area_add"))
async def area_add(m: Message, command: CommandObject):
    name = (command.args or "").strip()
    if not name:
        await m.answer("Формат: /area_add Название")
        return
    a = await core.areas()
    if name.lower() not in [x.lower() for x in a]:
        a.append(name)
        await core.db.set_areas(a)
    await m.answer("Сферы: " + e(", ".join(a)))


@owner.message(Command("area_del"))
async def area_del(m: Message, command: CommandObject):
    name = (command.args or "").strip().lower()
    a = [x for x in await core.areas() if x.lower() != name]
    await core.db.set_areas(a)
    await m.answer("Сферы: " + e(", ".join(a)))


# ---------- свободный текст и голос ----------
async def process(m: Message, text: str):
    edit_id = pending_edit.pop(m.from_user.id, None)
    await m.bot.send_chat_action(m.chat.id, "typing")
    try:
        out = await core.handle_text(text, edit_task_id=edit_id)
    except Exception:
        log.exception("parse failed")
        await m.answer("Не получилось разобрать — сервис ИИ не ответил. Попробуй ещё раз через минуту.")
        return
    await send_all(m.bot, m.chat.id, out)


@owner.message(F.voice | F.audio)
async def voice(m: Message):
    if not C.GROQ_API_KEY:
        await m.answer("Голосовые пока выключены: нужен GROQ_API_KEY.")
        return
    f = m.voice or m.audio
    buf = await m.bot.download(f.file_id)
    try:
        text = await transcribe(C.GROQ_API_KEY, buf.read())
    except Exception:
        log.exception("transcribe failed")
        await m.answer("Не смог распознать голосовое, попробуй ещё раз или напиши текстом.")
        return
    if not text:
        await m.answer("Голосовое пустое, ничего не расслышал.")
        return
    await m.answer(f"🎙 <i>{e(text)}</i>")
    await process(m, text)


@owner.message(F.text & ~F.text.startswith("/"))
async def text(m: Message):
    await process(m, m.text)


# ---------- кнопки ----------
@owner.callback_query()
async def buttons(q: CallbackQuery):
    parts = (q.data or "").split(":")
    act, tid = parts[0], int(parts[1])
    arg = parts[2] if len(parts) > 2 else ""
    done_text = None
    if act == "ok":
        await q.message.edit_reply_markup(reply_markup=None)
    elif act == "ed":
        pending_edit[q.from_user.id] = tid
        await q.message.answer("Напиши или скажи, что поправить (срок, время, название, сферу).")
    elif act == "del":
        done_text = await core.delete(tid)
    elif act == "d":
        done_text = await core.complete(tid)
    elif act == "m":
        done_text = await core.move(tid, arg)
    elif act == "s":
        done_text = await core.snooze(tid, arg)
    if done_text:
        await q.message.edit_text(done_text, reply_markup=None)
    await q.answer()


# ---------- фоновый цикл ----------
async def scheduler(bot: Bot):
    while True:
        try:
            await send_all(bot, C.OWNER_ID, await core.tick())
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(30)


async def main():
    global core
    missing = [k for k in ("BOT_TOKEN", "DATABASE_URL", "ANTHROPIC_API_KEY") if not getattr(C, k)]
    if missing:
        raise SystemExit(f"Не заданы переменные: {', '.join(missing)}")
    db = DB(C.DATABASE_URL)
    await db.connect()
    cal = None
    if C.GOOGLE_SERVICE_ACCOUNT_JSON and C.GOOGLE_CALENDAR_ID:
        from gcal import Calendar
        cal = Calendar(C.GOOGLE_SERVICE_ACCOUNT_JSON, C.GOOGLE_CALENDAR_ID, C.TZ, C.EVENT_MINUTES)
        log.info("calendar enabled")
    core = Core(db, C.TZ, C.DEFAULT_AREAS, C.DEFAULT_MORNING, C.DEFAULT_EVENING,
                parser=Parser(C.ANTHROPIC_API_KEY, C.CLAUDE_MODEL), cal=cal)

    bot = Bot(C.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    await bot.set_my_commands([
        BotCommand(command="today", description="Сегодня и просрочено"),
        BotCommand(command="week", description="Ближайшие 7 дней"),
        BotCommand(command="list", description="Все задачи"),
        BotCommand(command="settings", description="Настройки"),
        BotCommand(command="help", description="Как пользоваться"),
    ])
    dp = Dispatcher()
    dp.include_router(router)
    if C.OWNER_ID:
        asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
