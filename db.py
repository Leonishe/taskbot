import json
from datetime import date, datetime

import asyncpg

SCHEMA = """
create table if not exists tasks (
    id            serial primary key,
    title         text not null,
    area          text,
    due_date      date,
    remind_at     timestamptz,
    reminded      boolean not null default false,
    snooze_at     timestamptz,
    status        text not null default 'open',
    gcal_event_id text,
    created_at    timestamptz not null default now(),
    done_at       timestamptz
);
alter table tasks add column if not exists gcal_linked boolean not null default false;
create index if not exists tasks_open_idx on tasks (status, due_date);
create table if not exists jobs (
    id          serial primary key,
    run_at      timestamptz not null,
    instruction text not null,
    done        boolean not null default false
);
create table if not exists dialog (
    id        serial primary key,
    ts        timestamptz not null default now(),
    user_text text not null,
    result    text not null,
    ok        boolean not null default true,
    instruction text
);
create table if not exists settings (
    key   text primary key,
    value text not null
);
"""

FIELDS = {"title", "area", "due_date", "remind_at", "reminded", "snooze_at", "status", "gcal_event_id", "gcal_linked", "done_at"}


class DB:
    def __init__(self, url: str):
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)
        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)

    async def close(self):
        if self.pool:
            await self.pool.close()

    # ---- settings ----
    async def get(self, key: str, default=None):
        v = await self.pool.fetchval("select value from settings where key=$1", key)
        return default if v is None else v

    async def set(self, key: str, value: str):
        await self.pool.execute(
            "insert into settings(key,value) values($1,$2) "
            "on conflict(key) do update set value=excluded.value", key, value)

    async def get_areas(self, default: list[str]) -> list[str]:
        raw = await self.get("areas")
        return json.loads(raw) if raw else list(default)

    async def set_areas(self, areas: list[str]):
        await self.set("areas", json.dumps(areas, ensure_ascii=False))

    # ---- tasks ----
    async def add_task(self, title: str, area: str | None, due_date: date | None,
                       remind_at: datetime | None, reminded: bool = False) -> asyncpg.Record:
        return await self.pool.fetchrow(
            "insert into tasks(title, area, due_date, remind_at, reminded) values($1,$2,$3,$4,$5) returning *",
            title, area, due_date, remind_at, reminded)

    async def get_task(self, task_id: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow("select * from tasks where id=$1", task_id)

    async def update_task(self, task_id: int, **fields) -> asyncpg.Record | None:
        fields = {k: v for k, v in fields.items() if k in FIELDS}
        if not fields:
            return await self.get_task(task_id)
        cols = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        return await self.pool.fetchrow(
            f"update tasks set {cols} where id=$1 returning *", task_id, *fields.values())

    async def delete_task(self, task_id: int):
        await self.pool.execute("delete from tasks where id=$1", task_id)

    async def open_tasks(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "select * from tasks where status='open' "
            "order by due_date nulls last, remind_at nulls last, id")

    async def due_reminders(self, now: datetime) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "select * from tasks where status='open' and not reminded "
            "and coalesce(snooze_at, remind_at) <= $1 order by coalesce(snooze_at, remind_at)", now)

    # ---- отложенные поручения агенту ----
    async def add_job(self, run_at: datetime, instruction: str) -> int:
        return await self.pool.fetchval(
            "insert into jobs(run_at, instruction) values($1,$2) returning id", run_at, instruction)

    async def take_due_jobs(self, now: datetime) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "update jobs set done=true where not done and run_at <= $1 returning *", now)

    # ---- история диалога (контекст для «попробуй ещё», «а из Москвы?») ----
    async def add_dialog(self, user_text: str, result: str, ok: bool = True, instruction: str | None = None):
        await self.pool.execute("insert into dialog(user_text, result, ok, instruction) values($1,$2,$3,$4)",
                                user_text, result, ok, instruction)
        await self.pool.execute("delete from dialog where ts < now() - interval '30 days'")

    async def recent_dialog(self, since: datetime, limit: int = 8) -> list[asyncpg.Record]:
        rows = await self.pool.fetch(
            "select * from dialog where ts >= $1 order by id desc limit $2", since, limit)
        return list(reversed(rows))
