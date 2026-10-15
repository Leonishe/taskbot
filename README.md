# Бот задач — запуск на Railway

## 1. Ключи (≈10 минут)

| Что | Где взять | Переменная |
|---|---|---|
| Токен бота | @BotFather → /newbot | `BOT_TOKEN` |
| Ключ Claude | console.anthropic.com → API Keys (пополнить баланс на $5 — хватит надолго) | `ANTHROPIC_API_KEY` |
| Ключ для голосовых | console.groq.com → API Keys (бесплатно) | `GROQ_API_KEY` |

## 2. Railway

1. Залей эту папку в новый репозиторий на GitHub (на сайте GitHub: New repository → «uploading an existing file» → перетащи все файлы).
2. В твоём проекте на Railway: **New → GitHub Repo** → выбери этот репозиторий.
3. Там же: **New → Database → PostgreSQL**.
4. В сервисе бота → **Variables** добавь:
   - `BOT_TOKEN`
   - `ANTHROPIC_API_KEY`
   - `GROQ_API_KEY`
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
5. Бот запустится. Напиши ему /start — он пришлёт твой Telegram id.
6. Добавь переменную `OWNER_ID` = этот id. Railway перезапустит бота, и он начнёт работать. Остальным людям бот не отвечает.

## 3. Google Календарь (можно сделать позже)

1. console.cloud.google.com → создай проект (любое название).
2. **APIs & Services → Library** → найди **Google Calendar API** → Enable.
3. **IAM & Admin → Service Accounts → Create** → любое имя → Done.
4. Открой созданный аккаунт → **Keys → Add key → JSON** — скачается файл.
5. calendar.google.com → настройки твоего календаря → **Доступ для отдельных пользователей** → добавь email служебного аккаунта (из файла, поле `client_email`) с правом **«Вносить изменения в мероприятия»**.
6. В Railway добавь:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` = всё содержимое скачанного файла целиком
   - `GOOGLE_CALENDAR_ID` = твой gmail

В /settings бот покажет «Календарь: подключён».

## Необязательные переменные

- `TZ_NAME` — часовой пояс, по умолчанию `Europe/Minsk`
- `EVENT_MINUTES` — длина события в календаре, по умолчанию 30
- `CLAUDE_MODEL` — модель для разбора, по умолчанию `claude-haiku-5-5`

Важно: держи один экземпляр бота. Если запустить второй с тем же токеном, оба начнут сбоить.
