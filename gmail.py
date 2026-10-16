"""Чтение почты (только чтение). Рабочий ящик Google Workspace через служебный аккаунт с делегированием."""
import asyncio
import base64
import json
import logging
import re
from html import unescape

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

log = logging.getLogger("gmail")
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _b64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")


def _text(payload: dict) -> str:
    """Достать текст письма: plain, иначе html без тегов."""
    plain, html = [], []

    def walk(p):
        mt = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data and mt == "text/plain":
            plain.append(_b64(data))
        elif data and mt == "text/html":
            html.append(_b64(data))
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    h = "\n".join(html)
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"[ \t]+", " ", unescape(h))


class Gmail:
    def __init__(self, sa_json: str, user: str):
        creds = service_account.Credentials.from_service_account_info(
            json.loads(sa_json), scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        ).with_subject(user)
        self.s = AuthorizedSession(creds)

    def _search(self, query: str, n: int) -> list[dict]:
        r = self.s.get(f"{BASE}/messages", params={"q": query, "maxResults": n}, timeout=20)
        r.raise_for_status()
        out = []
        for m in r.json().get("messages", []):
            d = self.s.get(f"{BASE}/messages/{m['id']}", params={
                "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]}, timeout=20)
            d.raise_for_status()
            j = d.json()
            h = {x["name"]: x["value"] for x in j.get("payload", {}).get("headers", [])}
            out.append({"id": m["id"], "from": h.get("From"), "subject": h.get("Subject"),
                        "date": h.get("Date"), "snippet": unescape(j.get("snippet", ""))})
        return out

    def _read(self, msg_id: str, limit: int) -> dict:
        r = self.s.get(f"{BASE}/messages/{msg_id}", params={"format": "full"}, timeout=20)
        r.raise_for_status()
        j = r.json()
        h = {x["name"]: x["value"] for x in j.get("payload", {}).get("headers", [])}
        body = re.sub(r"\n\s*\n+", "\n\n", _text(j.get("payload", {}))).strip()
        return {"from": h.get("From"), "subject": h.get("Subject"), "date": h.get("Date"),
                "body": body[:limit] + ("…[обрезано]" if len(body) > limit else "")}

    async def search(self, query: str, n: int = 10) -> list[dict]:
        return await asyncio.to_thread(self._search, query, min(max(n, 1), 20))

    async def read(self, msg_id: str, limit: int = 12000) -> dict:
        return await asyncio.to_thread(self._read, msg_id, limit)
