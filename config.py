import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Твой Telegram id. Пока не задан — бот на /start просто покажет id.
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
DATABASE_URL = os.getenv("DATABASE_URL", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")

# Распознавание голоса (Groq Whisper). Без ключа голосовые не принимаются.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Google Calendar через служебный аккаунт. Без них бот работает без календаря.
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "")
EVENT_MINUTES = int(os.getenv("EVENT_MINUTES", "30"))

TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Minsk"))

DEFAULT_AREAS = ["EQLBM", "Рейкбек", "Сайт и контент", "Личное"]
DEFAULT_MORNING = "12:00"
DEFAULT_EVENING = "18:00"

# До этого часа «сегодня/завтра» считаются днём после сна (ты ещё не ложился).
DAY_START_HOUR = int(os.getenv("DAY_START_HOUR", "6"))

# Поручения (агент)
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")
TRAVELPAYOUTS_TOKEN = os.getenv("TRAVELPAYOUTS_TOKEN", "")
GMAIL_USER = os.getenv("GMAIL_USER", "")  # ящик Workspace, напр. poker@eqlbm.club

# CRM (веб-страница + данные для бота)
# Логины и пароли: "leo:пароль1,helper:пароль2". Без этой переменной веб-страница CRM не запускается.
CRM_USERS = dict(x.split(":", 1) for x in os.getenv("CRM_USERS", "").split(",") if ":" in x)
CRM_USERS = {k.strip().lower(): v.strip() for k, v in CRM_USERS.items()}
CRM_SECRET = os.getenv("CRM_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))
CRM_API_TOKEN = os.getenv("CRM_API_TOKEN", "")  # ключ для доступа Claude к CRM из чата
