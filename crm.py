"""CRM EQLBM: хранилище документов (как было в артефакте) + расчёты для бота.

Данные лежат в Postgres в виде коллекций документов: people, payments, rake, rooms, tasks, expenses.
Страница CRM работает с ними через /api, бот — напрямую через этот модуль.
"""
import json
import secrets
import time
from datetime import date, datetime, timedelta

COLLECTIONS = ["people", "payments", "rake", "rooms", "tasks", "expenses"]

SCHEMA = """
create table if not exists crm_docs (
    col        text not null,
    id         text not null,
    data       jsonb not null,
    updated_at timestamptz not null default now(),
    updated_by text,
    primary key (col, id)
);
create table if not exists crm_rev (k int primary key, rev bigint not null);
insert into crm_rev values (1, 0) on conflict do nothing;
"""

STAGES = {"new": "Новый", "talk": "В диалоге", "think": "Думает", "won_club": "Оплатил клуб",
          "won_eqrb": "Играет по сделке", "lost": "Отказ"}
TAGS = {"lead": "Лид", "club": "Клуб", "student": "Ученик", "eqrb": "EQRB"}
PRICE = 349


def uid() -> str:
    return format(int(time.time() * 1000), "x") + secrets.token_hex(3)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _merge(old: dict, new: dict) -> dict:
    """Слияние для update: вложенные словари сливаются, остальное заменяется."""
    out = dict(old)
    for k, v in new.items():
        if isinstance(v, dict) and v.get("__delete__") is True:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class CRM:
    def __init__(self, db, tz):
        self.db = db  # наш DB с пулом
        self.tz = tz

    async def init(self):
        async with self.db.pool.acquire() as c:
            await c.execute(SCHEMA)

    # ---------- хранилище ----------
    async def rev(self) -> int:
        return await self.db.pool.fetchval("select rev from crm_rev where k=1")

    async def all(self) -> dict:
        rows = await self.db.pool.fetch("select col, id, data from crm_docs")
        out = {c: {} for c in COLLECTIONS}
        for r in rows:
            out.setdefault(r["col"], {})[r["id"]] = json.loads(r["data"])
        return out

    async def get(self, col: str, id_: str) -> dict | None:
        v = await self.db.pool.fetchval("select data from crm_docs where col=$1 and id=$2", col, id_)
        return json.loads(v) if v else None

    async def write(self, op: str, col: str, id_: str, data: dict | None = None, by: str = "") -> bool:
        if col not in COLLECTIONS or not id_ or len(id_) > 200:
            raise ValueError("bad collection or id")
        async with self.db.pool.acquire() as c:
            async with c.transaction():
                if op == "set":
                    await c.execute(
                        "insert into crm_docs(col,id,data,updated_by) values($1,$2,$3,$4) "
                        "on conflict(col,id) do update set data=excluded.data, updated_at=now(), updated_by=excluded.updated_by",
                        col, id_, json.dumps(data or {}, ensure_ascii=False), by)
                elif op == "update":
                    cur = await c.fetchval("select data from crm_docs where col=$1 and id=$2 for update", col, id_)
                    if cur is None:
                        return False
                    new = _merge(json.loads(cur), data or {})
                    await c.execute("update crm_docs set data=$3, updated_at=now(), updated_by=$4 where col=$1 and id=$2",
                                    col, id_, json.dumps(new, ensure_ascii=False), by)
                elif op == "delete":
                    await c.execute("delete from crm_docs where col=$1 and id=$2", col, id_)
                else:
                    raise ValueError("bad op")
                await c.execute("update crm_rev set rev=rev+1 where k=1")
        return True

    async def is_empty(self) -> bool:
        return not await self.db.pool.fetchval("select exists(select 1 from crm_docs)")

    async def import_dump(self, dump: dict, by: str = "import") -> int:
        n = 0
        async with self.db.pool.acquire() as c:
            async with c.transaction():
                await c.execute("delete from crm_docs")
                for col, docs in dump.items():
                    if col not in COLLECTIONS:
                        continue
                    for id_, d in docs.items():
                        await c.execute("insert into crm_docs(col,id,data,updated_by) values($1,$2,$3,$4)",
                                        col, id_, json.dumps(d, ensure_ascii=False), by)
                        n += 1
                await c.execute("update crm_rev set rev=rev+1 where k=1")
        return n

    # ---------- расчёты (повторяют логику страницы) ----------
    def today(self) -> str:
        return datetime.now(self.tz).date().isoformat()

    @staticmethod
    def room_name(S, rid) -> str:
        if not rid:
            return "—"
        if rid.startswith("x:"):
            return rid[2:]
        return (S["rooms"].get(rid) or {}).get("name") or "Удалённый рум"

    @staticmethod
    def pname(S, pid) -> str:
        return (S["people"].get(pid) or {}).get("name") or "Без имени"

    @staticmethod
    def rcalc(S, r: dict) -> dict:
        room = S["rooms"].get(r.get("room")) or {}
        deal = r.get("deal") if r.get("deal") is not None else room.get("deal", 0)
        pay = r.get("payout") if r.get("payout") is not None else room.get("payout", 0)
        rake = _num(r.get("rake"))
        inc = rake * _num(deal) / 100
        of = r.get("outFixed")
        out = _num(of) if of not in (None, "") else rake * _num(pay) / 100
        rf = (S["people"].get(r.get("personId")) or {}).get("ref") or {}
        ref_pid = None
        if rf.get("pid") and _num(rf.get("pct")) and rf["pid"] in S["people"] and (
                not rf.get("since") or r.get("month", "") >= rf["since"]):
            ref_pid = rf["pid"]
        ref = rake * _num(rf.get("pct")) / 100 if ref_pid else 0.0
        return {"deal": _num(deal), "pay": _num(pay), "inc": inc, "out": out, "ref": ref,
                "ref_pid": ref_pid, "margin": inc - out - ref}

    def club_state(self, p: dict) -> dict | None:
        c = p.get("club") or {}
        if "club" not in (p.get("tags") or []) or not c.get("access") or c["access"] == "none":
            return None
        if c["access"] != "paid":
            return {"key": "free", "label": {"eqrb": "Бесплатно (EQRB)", "og": "OG"}.get(c["access"], c["access"])}
        if not c.get("until"):
            return {"key": "nodate", "label": "Нет даты окончания"}
        left = (date.fromisoformat(c["until"]) - date.fromisoformat(self.today())).days
        if left < 0:
            return {"key": "bad", "label": f"Просрочено {-left} дн", "left": left}
        if left <= 7:
            return {"key": "warn", "label": "Заканчивается сегодня" if left == 0 else f"Продление через {left} дн", "left": left}
        return {"key": "ok", "label": f"Активна до {c['until']}", "left": left}

    def attention(self, S: dict) -> dict:
        t = self.today()
        subs = []
        for pid, p in S["people"].items():
            s = self.club_state(p)
            if s and (s["key"] in ("bad", "nodate") or (s.get("left") is not None and s["left"] <= 14)):
                subs.append({"person_id": pid, "name": p.get("name"), "until": (p.get("club") or {}).get("until"), "status": s["label"]})
        rb: dict = {}
        pd: dict = {}
        for r in S["rake"].values():
            c = self.rcalc(S, r)
            if not r.get("paid") and c["out"] > 0.004:
                x = rb.setdefault(r.get("personId"), {"sum": 0.0, "months": set()})
                x["sum"] += c["out"]; x["months"].add(r.get("month"))
            if c["ref_pid"] and not r.get("refPaid") and c["ref"] > 0.004:
                x = rb.setdefault(c["ref_pid"], {"sum": 0.0, "months": set()})
                x["sum"] += c["ref"]; x["months"].add(r.get("month"))
            if r.get("partnerUnpaid"):
                k = (S["rooms"].get(r.get("room")) or {}).get("partner") or self.room_name(S, r.get("room"))
                x = pd.setdefault(k, {"sum": 0.0, "months": set()})
                x["sum"] += c["inc"]; x["months"].add(r.get("month"))
        leads = [{"person_id": pid, "name": p.get("name"), "next": (p.get("lead") or {}).get("next"),
                  "date": (p.get("lead") or {}).get("nextDate")}
                 for pid, p in S["people"].items()
                 if "lead" in (p.get("tags") or []) and (p.get("lead") or {}).get("nextDate")
                 and p["lead"]["nextDate"] <= t and p["lead"].get("stage") not in ("won_club", "won_eqrb", "lost")]
        tasks = [{"task_id": tid, "title": x.get("title"), "due": x.get("due"), "who": x.get("who") or "me",
                  "person": self.pname(S, x["personId"]) if x.get("personId") in S["people"] else None}
                 for tid, x in S["tasks"].items() if not x.get("done") and x.get("due") and x["due"] <= t]
        bdays = []
        today_d = date.fromisoformat(t)
        for pid, p in S["people"].items():
            b = p.get("birthday")
            if not b:
                continue
            try:
                _, mm, dd = map(int, b.split("-"))
                n = date(today_d.year, mm, dd)
                if n < today_d:
                    n = date(today_d.year + 1, mm, dd)
            except ValueError:
                continue
            if (n - today_d).days <= 7:
                bdays.append({"name": p.get("name"), "in_days": (n - today_d).days})
        fmt = lambda d: [{"person_id": k, "name": self.pname(S, k), "sum": round(v["sum"], 2),
                          "months": sorted(m for m in v["months"] if m)} for k, v in d.items()]
        return {
            "subscriptions": sorted(subs, key=lambda x: x["until"] or ""),
            "rakeback_to_pay": sorted(fmt(rb), key=lambda x: -x["sum"]),
            "partners_owe": [{"partner": k, "sum": round(v["sum"], 2), "months": sorted(m for m in v["months"] if m)} for k, v in pd.items()],
            "leads_due": leads, "tasks_due": tasks, "birthdays": bdays,
        }

    def rake_report(self, S: dict, months: list[str] | None = None, person_id: str | None = None) -> dict:
        rows, per = [], {}
        T = {"rake": 0.0, "income": 0.0, "to_players": 0.0, "margin": 0.0, "partner_unpaid": 0.0}
        for rid, r in S["rake"].items():
            if months and r.get("month") not in months:
                continue
            c = self.rcalc(S, r)
            if person_id and r.get("personId") != person_id and c["ref_pid"] != person_id:
                continue
            row = {"id": rid, "month": r.get("month"), "player": self.pname(S, r.get("personId")), "nick": r.get("nick"),
                   "room": self.room_name(S, r.get("room")), "rake": _num(r.get("rake")), "deal_pct": c["deal"],
                   "player_pct": c["pay"], "income": round(c["inc"], 2), "to_player": round(c["out"], 2),
                   "referral": round(c["ref"], 2), "referrer": self.pname(S, c["ref_pid"]) if c["ref_pid"] else None,
                   "margin": round(c["margin"], 2), "paid": bool(r.get("paid")),
                   "partner_unpaid": bool(r.get("partnerUnpaid")), "note": r.get("note")}
            rows.append(row)
            x = per.setdefault(row["player"], {"rake": 0.0, "income": 0.0, "to_player": 0.0, "margin": 0.0})
            for k in x:
                x[k] = round(x[k] + row[k], 2)
            T["rake"] += row["rake"]; T["income"] += c["inc"]; T["to_players"] += c["out"] + c["ref"]
            T["margin"] += c["margin"]
            if row["partner_unpaid"]:
                T["partner_unpaid"] += c["inc"]
        rows.sort(key=lambda x: (x["month"] or "", x["player"]))
        return {"totals": {k: round(v, 2) for k, v in T.items()}, "by_player": per, "rows": rows[:200]}

    def find_people(self, S: dict, q: str) -> list[dict]:
        q = (q or "").strip().lower()
        out = []
        for pid, p in S["people"].items():
            hay = " ".join(str(x or "") for x in [p.get("name"), p.get("tg"), p.get("discord"), p.get("phone"), p.get("note")]
                           + [a.get("nick") for a in p.get("accounts") or []]).lower()
            if not q or q in hay:
                out.append({"person_id": pid, "name": p.get("name"), "tags": p.get("tags"), "tg": p.get("tg"),
                            "nicks": [f"{a.get('nick')} ({self.room_name(S, a.get('room'))})" for a in p.get("accounts") or [] if a.get("nick")],
                            "club": (self.club_state(p) or {}).get("label"),
                            "lead_stage": STAGES.get((p.get("lead") or {}).get("stage")) if "lead" in (p.get("tags") or []) else None})
        return out[:50]

    def person_card(self, S: dict, pid: str) -> dict | None:
        p = S["people"].get(pid)
        if not p:
            return None
        pays = [x for x in S["payments"].values() if x.get("personId") == pid]
        return {"person_id": pid, **p, "club_status": (self.club_state(p) or {}).get("label"),
                "payments": sorted(pays, key=lambda x: x.get("date") or "", reverse=True),
                "rake": self.rake_report(S, person_id=pid)["rows"],
                "tasks": [{"task_id": k, **v} for k, v in S["tasks"].items() if v.get("personId") == pid]}

    # ---------- действия бота ----------
    async def add_rake(self, person_id: str, room_id: str, month: str, rake: float, note: str = "", by: str = "bot") -> dict:
        S = await self.all()
        p, room = S["people"].get(person_id), S["rooms"].get(room_id)
        if not p:
            return {"error": "нет такого человека"}
        if not room:
            return {"error": "нет такого рума со сделкой", "rooms": {k: v.get("name") for k, v in S["rooms"].items()}}
        acc = next((a for a in p.get("accounts") or [] if a.get("room") == room_id), None)
        dup = next(((k, r) for k, r in S["rake"].items()
                    if r.get("personId") == person_id and r.get("room") == room_id and r.get("month") == month), None)
        if dup:
            total = round(_num(dup[1].get("rake")) + rake, 2)
            await self.write("update", "rake", dup[0], {"rake": total}, by)
            return {"ok": True, "added_to_existing": True, "rake_now": total}
        pick = lambda k: _num(acc[k]) if acc and acc.get(k) not in ("", None) else _num(room.get(k))
        doc = {"personId": person_id, "month": month, "room": room_id, "nick": (acc or {}).get("nick", ""),
               "rake": rake, "deal": pick("deal"), "payout": pick("payout"), "paid": False, "paidDate": "",
               "createdAt": self.today()}
        if note:
            doc["note"] = note
        rid = uid()
        await self.write("set", "rake", rid, doc, by)
        S["rake"][rid] = doc
        c = self.rcalc(S, doc)
        return {"ok": True, "id": rid, "income": round(c["inc"], 2), "to_player": round(c["out"], 2), "margin": round(c["margin"], 2)}

    async def mark_paid(self, person_id: str, by: str = "bot") -> dict:
        S = await self.all()
        n, total = 0, 0.0
        for rid, r in S["rake"].items():
            c = self.rcalc(S, r)
            if r.get("personId") == person_id and not r.get("paid") and c["out"] > 0.004:
                await self.write("update", "rake", rid, {"paid": True, "paidDate": self.today()}, by); n += 1; total += c["out"]
            if c["ref_pid"] == person_id and not r.get("refPaid"):
                await self.write("update", "rake", rid, {"refPaid": True, "refPaidDate": self.today()}, by); n += 1; total += c["ref"]
        return {"ok": True, "records": n, "sum": round(total, 2)}

    async def add_person(self, fields: dict, by: str = "bot") -> dict:
        if not (fields.get("name") or "").strip():
            return {"error": "нужно имя"}
        doc = {"name": fields["name"].strip(), "tags": fields.get("tags") or ["lead"], "createdAt": self.today()}
        for k in ("tg", "discord", "phone", "source", "note", "birthday", "usdt"):
            if fields.get(k):
                doc[k] = fields[k]
        if "lead" in doc["tags"]:
            doc["lead"] = {"stage": fields.get("stage") or "new", "interest": fields.get("interest") or "club"}
            if fields.get("next"):
                doc["lead"]["next"] = fields["next"]
            if fields.get("next_date"):
                doc["lead"]["nextDate"] = fields["next_date"]
        pid = uid()
        await self.write("set", "people", pid, doc, by)
        return {"ok": True, "person_id": pid}

    async def add_task(self, title: str, due: str = "", who: str = "me", person_id: str = "", by: str = "bot") -> dict:
        tid = uid()
        await self.write("set", "tasks", tid, {"title": title, "due": due or "", "personId": person_id or "",
                                               "who": who if who in ("me", "helper") else "me",
                                               "done": False, "createdAt": self.today()}, by)
        return {"ok": True, "task_id": tid}

    # ---------- для утренней сводки ----------
    async def morning_lines(self) -> list[str]:
        S = await self.all()
        A = self.attention(S)
        L = []
        for s in A["subscriptions"]:
            L.append(f"• Подписка: {s['name']} — {s['status']}")
        for x in A["leads_due"]:
            L.append(f"• Лид {x['name']}: {x['next'] or 'следующий шаг'}" + (" (просрочено)" if x["date"] < self.today() else ""))
        for x in A["tasks_due"]:
            who = " [помощник]" if x["who"] == "helper" else ""
            L.append(f"• {x['title']}{who}" + (" (просрочено)" if x["due"] < self.today() else ""))
        if A["rakeback_to_pay"]:
            tot = sum(x["sum"] for x in A["rakeback_to_pay"])
            L.append(f"• Выплатить рейкбек: {len(A['rakeback_to_pay'])} чел. на ${tot:,.2f}".replace(",", " "))
        for b in A["birthdays"]:
            L.append(f"• 🎂 {b['name']} — " + ("сегодня" if b["in_days"] == 0 else "завтра" if b["in_days"] == 1 else f"через {b['in_days']} дн"))
        return L
