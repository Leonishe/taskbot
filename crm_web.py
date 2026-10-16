"""Веб-часть CRM: вход по паролю, страница и API для неё. Работает в том же процессе, что и бот."""
import asyncio
import hashlib
import hmac
import html
import json
import logging
import time
from pathlib import Path

from aiohttp import web

log = logging.getLogger("crm_web")
PAGE = (Path(__file__).parent / "crm_page.html").read_text(encoding="utf-8")
COOKIE = "eqlbm_crm"
TTL = 60 * 24 * 3600

LOGIN = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EQLBM CRM — вход</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500;600&family=Unbounded:wght@500&display=swap">
<style>
:root{--bg:#F2F3F7;--surface:#fff;--line:#D9DCE5;--fg:#161923;--muted:#5F6577;--accent:#4A3ECF;--bad:#BE3A33;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0F1117;--surface:#171A23;--line:#2B3041;--fg:#E7E9F0;--muted:#959BAD;--accent:#8F86FF;--bad:#F07169;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:15px Onest,system-ui,sans-serif;padding:16px}
form{width:min(360px,100%);background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:24px;display:flex;flex-direction:column;gap:12px}
h1{font:500 18px Unbounded,Onest,sans-serif;margin:0 0 6px}h1 span{font-size:11px;color:var(--muted);letter-spacing:.14em;margin-left:6px}
input{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:10px 12px;width:100%}
input:focus{outline:none;border-color:var(--accent)}
button{font:inherit;font-weight:600;border:0;border-radius:9px;padding:11px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--bad);font-size:13.5px;margin:0}
</style></head><body>
<form method="post" action="/login"><h1>EQLBM<span>CRM</span></h1>
__ERR__<input name="user" placeholder="Логин" autocomplete="username" autocapitalize="off" required autofocus>
<input name="password" type="password" placeholder="Пароль" autocomplete="current-password" required>
<button>Войти</button></form></body></html>"""


class CRMWeb:
    def __init__(self, crm, users: dict[str, str], secret: str, api_token: str = ""):
        self.crm = crm
        self.api_token = api_token
        self.users = users  # логин -> пароль
        self.secret = secret.encode()

    # ---------- сессии ----------
    def _sign(self, user: str, exp: int) -> str:
        msg = f"{user}|{exp}"
        return msg + "|" + hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()

    def user_of(self, req: web.Request) -> str | None:
        auth = req.headers.get("Authorization", "")
        if self.api_token and len(self.api_token) >= 24 and auth.startswith("Bearer "):
            return "claude" if hmac.compare_digest(auth[7:].strip(), self.api_token) else None
        raw = req.cookies.get(COOKIE, "")
        try:
            user, exp, sig = raw.rsplit("|", 2)
            ok = hmac.compare_digest(self._sign(user, int(exp)), raw)
        except ValueError:
            return None
        if not ok or int(exp) < time.time() or user not in self.users:
            return None
        return user

    # ---------- страницы ----------
    async def login_page(self, req):
        return web.Response(text=LOGIN.replace("__ERR__", ""), content_type="text/html")

    async def login(self, req):
        form = await req.post()
        u, p = (form.get("user") or "").strip().lower(), form.get("password") or ""
        if u in self.users and hmac.compare_digest(self.users[u], p):
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE, self._sign(u, int(time.time()) + TTL), max_age=TTL,
                            httponly=True, secure=req.secure or req.headers.get("X-Forwarded-Proto") == "https",
                            samesite="Strict")
            raise resp
        await asyncio.sleep(1.5)  # тормозим перебор паролей
        return web.Response(text=LOGIN.replace("__ERR__", '<p class="err">Неверный логин или пароль</p>'),
                            content_type="text/html", status=401)

    async def logout(self, req):
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE)
        raise resp

    async def index(self, req):
        user = self.user_of(req)
        if not user:
            raise web.HTTPFound("/login")
        return web.Response(text=PAGE.replace("__CRM_USER__", html.escape(user)), content_type="text/html",
                            headers={"Cache-Control": "no-store"})

    # ---------- API ----------
    def _need(self, req) -> str:
        user = self.user_of(req)
        if not user:
            raise web.HTTPUnauthorized(text='{"error":"auth"}', content_type="application/json")
        return user

    async def api_rev(self, req):
        self._need(req)
        return web.json_response({"rev": await self.crm.rev()}, headers={"Cache-Control": "no-store"})

    async def api_data(self, req):
        self._need(req)
        rev = await self.crm.rev()
        return web.json_response({"rev": rev, "data": await self.crm.all()}, headers={"Cache-Control": "no-store"},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def api_doc(self, req):
        user = self._need(req)
        if req.content_type != "application/json":
            return web.json_response({"error": "bad content type"}, status=400)
        b = await req.json()
        try:
            ok = await self.crm.write(b.get("op"), b.get("col"), str(b.get("id") or ""), b.get("data"), by=user)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        if not ok:
            return web.json_response({"error": "документ не найден"}, status=404)
        return web.json_response({"ok": True})

    async def health(self, req):
        return web.Response(text="ok")

    def app(self) -> web.Application:
        a = web.Application(client_max_size=2 * 1024 * 1024)
        a.add_routes([
            web.get("/", self.index), web.get("/login", self.login_page), web.post("/login", self.login),
            web.get("/logout", self.logout), web.get("/api/rev", self.api_rev), web.get("/api/data", self.api_data),
            web.post("/api/doc", self.api_doc), web.get("/health", self.health),
        ])
        return a


async def start(crm, users: dict[str, str], secret: str, port: int, api_token: str = ""):
    runner = web.AppRunner(CRMWeb(crm, users, secret, api_token).app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("CRM web on :%s", port)
    return runner
