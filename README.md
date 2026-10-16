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

## 4. Поручения (поиск, билеты, почта)

Поиск в интернете работает сразу, отдельно ничего настраивать не нужно.

**Билеты.** travelpayouts.com → регистрация → если попросит проект, укажи «Telegram-бот» → Профиль → **API token**. В Railway: `TRAVELPAYOUTS_TOKEN` = этот токен.

**Почта (ящик Workspace).**
1. В Google Cloud, проект eqtaskbot: в поиске **Gmail API** → Enable.
2. admin.google.com → Безопасность → Доступ к данным и управление ими → **Управление API** → **Делегирование доступа на уровне домена** → Добавить:
   - Client ID: `114192175002995948206` (OAuth 2 Client ID служебного аккаунта)
   - Области: `https://www.googleapis.com/auth/gmail.readonly`
3. В Railway: `GMAIL_USER` = `poker@eqlbm.club`.

Бот получает доступ только на чтение: писать и удалять письма он не может.

## 5. CRM на Railway

1. В Railway, сервис бота → **Variables**: `CRM_USERS` = `leo:ПАРОЛЬ1,helper:ПАРОЛЬ2` (пароли придумай сам; `leo` — твой вход, `helper` — помощника).
2. Сервис бота → **Settings → Networking → Generate Domain**. Если спросит порт — `8080`. Получишь адрес вида `taskbot-production.up.railway.app` — это и есть CRM.
3. Отправь боту файл `crm-data.json` с подписью `/crm_import` — данные загрузятся.
4. Открой адрес из п.2, войди своим логином.

Резервная копия в любой момент: команда /crm_export — бот пришлёт файл со всеми данными.
Помощник видит те же данные; в разделе «Задачи» у него по умолчанию открыты его задачи.

## Необязательные переменные

- `TZ_NAME` — часовой пояс, по умолчанию `Europe/Minsk`
- `EVENT_MINUTES` — длина события в календаре, по умолчанию 30
- `CLAUDE_MODEL` — модель для разбора, по умолчанию `claude-haiku-5-5`
- `AGENT_MODEL` — модель для поручений, по умолчанию `claude-sonnet-5-5`
- `CRM_SECRET` — ключ подписи входа в CRM (по умолчанию выводится из токена бота)
- `DAY_START_HOUR` — до этого часа «завтра» значит день после сна, по умолчанию 6

Важно: держи один экземпляр бота. Если запустить второй с тем же токеном, оба начнут сбоить.
