"""Разбор свободного текста в действия с задачами (Claude) и распознавание голоса (Groq Whisper)."""
from datetime import datetime, timedelta

import httpx
from anthropic import AsyncAnthropic

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

TOOL = {
    "name": "apply_actions",
    "description": "Применить действия к списку задач пользователя.",
    "input_schema": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["add", "complete", "edit", "delete", "list"]},
                        "task_id": {"type": "integer", "description": "Для complete/edit/delete: id существующей задачи"},
                        "title": {"type": "string", "description": "Короткая формулировка задачи, с глагола, без даты и времени"},
                        "area": {"type": "string", "description": "Одна из сфер пользователя"},
                        "due_date": {"type": "string", "description": "YYYY-MM-DD или пустая строка, если срока нет"},
                        "time": {"type": "string", "description": "HH:MM, если названо конкретное время, иначе пустая строка"},
                        "period": {"type": "string", "enum": ["today", "tomorrow", "week", "overdue", "all"]},
                    },
                    "required": ["type"],
                },
            },
            "reply": {"type": "string", "description": "Короткий ответ, если действий нет или нужно уточнение"},
        },
        "required": ["actions"],
    },
}


def build_system(now: datetime, areas: list[str], tasks: list) -> str:
    days = "\n".join(
        f"{(now + timedelta(days=i)).date().isoformat()} — {WEEKDAYS[(now + timedelta(days=i)).weekday()]}"
        + (" (сегодня)" if i == 0 else " (завтра)" if i == 1 else "")
        for i in range(15))
    task_lines = "\n".join(
        f"#{t['id']} | {t['title']} | {t['area'] or '-'} | {t['due_date'] or 'без срока'}"
        for t in tasks[:150]) or "(задач нет)"
    return f"""Ты — разборщик сообщений в Telegram-боте задач. Пользователь пишет коротко, по-русски, как в заметки.
Сейчас: {now.strftime('%Y-%m-%d %H:%M')}, {WEEKDAYS[now.weekday()]}.

Календарь на ближайшие дни:
{days}

Сферы пользователя: {', '.join(areas)}

Открытые задачи:
{task_lines}

Правила:
- Новая задача или несколько задач в одном сообщении → по action "add" на каждую.
- title: коротко, с глагола, без даты/времени («Скинуть Кириллу условия по QQ»).
- area: выбери самую подходящую из списка сфер. EQLBM — клуб, обучение, игроки, комьюнити. Рейкбек — румы, аффилейт, агенты, выплаты. Сайт и контент — сайт, Instagram, посты, видео. Личное — всё остальное. Если сферы другие — ориентируйся по смыслу.
- due_date: если срок назван («завтра», «в пятницу», «до конца месяца» → последний день месяца) — дата YYYY-MM-DD. Нет срока — пустая строка.
- time: только если названо конкретное время («в 15:00», «в 3 дня» → 15:00, «вечером» без времени → пустая строка). Если время назвали без даты — due_date сегодня, а если это время уже прошло — завтра.
- «Напомни через 2 часа» → due_date и time = сейчас + 2 часа.
- Сообщение о том, что задача сделана («сделал», «отправил», «готово») → "complete" с task_id подходящей открытой задачи.
- Просьба перенести/изменить задачу → "edit" с task_id и только изменёнными полями; неизменённые поля не передавай. Чтобы убрать срок или время, передай в due_date или time значение "none".
- Просьба удалить → "delete".
- Вопрос «что у меня…», «что по EQLBM», «что на неделе» → "list" с period и, если названа, area.
- Если не понятно, к какой задаче относится complete/edit/delete — не угадывай, верни пустой actions и короткий вопрос в reply.
Всегда отвечай вызовом apply_actions."""


class Parser:
    def __init__(self, api_key: str, model: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def parse(self, text: str, now: datetime, areas: list[str], tasks: list) -> dict:
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=build_system(now, areas, tasks),
            tools=[TOOL],
            tool_choice={"type": "tool", "name": "apply_actions"},
            messages=[{"role": "user", "content": text}],
        )
        for block in msg.content:
            if block.type == "tool_use":
                return block.input
        return {"actions": [], "reply": "Не понял, переформулируй, пожалуйста."}


async def transcribe(api_key: str, audio: bytes, filename: str = "voice.ogg") -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": "whisper-large-v3-turbo", "language": "ru", "response_format": "json"},
            files={"file": (filename, audio, "audio/ogg")},
        )
        r.raise_for_status()
        return r.json().get("text", "").strip()
