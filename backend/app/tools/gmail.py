"""Gmail through the Gmail REST API (read + send scopes only).

Sign in once with `python scripts/gmail_login.py`; the refresh token is kept in
backend/data/gmail-token.json (git-ignored). Mail text is written by other
people, so results put it under "untrusted_content" and the system prompt
tells Mario never to act on instructions found there. Sending always goes
through the voice confirmation in the tool registry.
"""

import asyncio
import base64
import html
import json
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path

import httpx

from ..config import Settings

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
# Asked for by scripts/gmail_login.py along with Gmail; read-only.
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
LOGIN_SCOPES = SCOPES + CALENDAR_SCOPES
BODY_LIMIT = 3000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class NotConnected(Exception):
    pass


def is_connected(settings: Settings) -> bool:
    return settings.gmail_token_file.exists()


def _token(settings: Settings) -> str:
    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2.credentials import Credentials  # noqa: PLC0415

    path: Path = settings.gmail_token_file
    if not path.exists():
        raise NotConnected
    # No scopes passed: the refresh keeps whatever the user granted at login.
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if not creds.refresh_token:
            raise NotConnected
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return creds.token


def _client(settings: Settings) -> httpx.Client:
    token = "test-token" if transport else _token(settings)
    return httpx.Client(
        base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=20, transport=transport
    )


def _call(fn, settings: Settings, *args) -> dict:
    """Runs a sync Gmail helper and turns setup/network problems into spoken errors."""
    try:
        with _client(settings) as client:
            return fn(client, *args)
    except NotConnected:
        return {
            "status": "error",
            "message": "Gmail bağlı değil. Bilgisayarda scripts\\gmail_login.py bir kez çalıştırılmalı.",
        }
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"status": "error", "message": "Bu e-posta bulunamadı."}
        return {
            "status": "error",
            "message": f"Gmail isteği başarısız oldu ({exc.response.status_code}).",
        }
    except httpx.HTTPError as exc:
        return {"status": "error", "message": f"Gmail'e ulaşılamadı ({exc.__class__.__name__})."}


def _headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _sender(value: str) -> str:
    name, addr = parseaddr(value)
    return name or addr


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def body_text(payload: dict) -> str:
    """Plain-text body; falls back to the HTML part with tags stripped."""
    plain_parts, html_parts = [], []

    def walk(part: dict) -> None:
        data = part.get("body", {}).get("data")
        if data and part.get("mimeType") == "text/plain":
            plain_parts.append(_decode(data))
        elif data and part.get("mimeType") == "text/html":
            html_parts.append(_decode(data))
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    if plain_parts:
        text = "\n".join(plain_parts)
    else:
        text = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(html_parts), flags=re.S | re.I
        )
        text = html.unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def _list(client: httpx.Client, query: str, limit: int) -> dict:
    found = (
        client.get("/messages", params={"q": query, "maxResults": limit}).raise_for_status().json()
    )
    items = []
    for ref in found.get("messages", []):
        msg = (
            client.get(
                f"/messages/{ref['id']}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            .raise_for_status()
            .json()
        )
        h = _headers(msg)
        items.append(
            {
                "id": msg["id"],
                "from": _sender(h.get("from", "")),
                "subject": h.get("subject", "(konusuz)"),
                "date": h.get("date", ""),
                "snippet": html.unescape(msg.get("snippet", "")),
            }
        )
    if not items:
        return {"status": "ok", "message": "Bu aramaya uyan e-posta yok.", "emails": []}
    lines = "; ".join(f"{i['from']}: {i['subject']}" for i in items)
    return {
        "status": "ok",
        "message": f"{len(items)} e-posta: {lines}.",
        "untrusted_content": True,
        "emails": items,
    }


def _read(client: httpx.Client, message_id: str) -> dict:
    msg = client.get(f"/messages/{message_id}", params={"format": "full"}).raise_for_status().json()
    h = _headers(msg)
    body = body_text(msg.get("payload", {}))
    return {
        "status": "ok",
        "message": f"{_sender(h.get('from', ''))} gönderdi, konu: {h.get('subject', '(konusuz)')}.",
        "id": msg["id"],
        "from": h.get("from", ""),
        "subject": h.get("subject", ""),
        "date": h.get("date", ""),
        "untrusted_content": True,
        "body": body[:BODY_LIMIT] + (" …(kısaltıldı)" if len(body) > BODY_LIMIT else ""),
    }


def _find_address(client: httpx.Client, name: str) -> dict:
    found = (
        client.get("/messages", params={"q": f"from:({name}) OR to:({name})", "maxResults": 10})
        .raise_for_status()
        .json()
    )
    wanted = name.casefold()
    seen: dict[str, str] = {}
    for ref in found.get("messages", []):
        msg = (
            client.get(
                f"/messages/{ref['id']}",
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "To", "Cc"],
                },
            )
            .raise_for_status()
            .json()
        )
        h = _headers(msg)
        for display, addr in getaddresses([h.get("from", ""), h.get("to", ""), h.get("cc", "")]):
            if addr and (wanted in display.casefold() or wanted in addr.casefold()):
                seen.setdefault(addr.lower(), display or addr)
    if not seen:
        return {
            "status": "error",
            "message": f"'{name}' için bir e-posta adresi bulamadım. Adresi söyleyebilir misin?",
        }
    options = [f"{display} <{addr}>" for addr, display in seen.items()]
    return {
        "status": "ok" if len(options) == 1 else "ambiguous",
        "message": ("Adres: " if len(options) == 1 else "Birden fazla adres var: ")
        + ", ".join(options)
        + ".",
        "addresses": list(seen),
    }


def _send(client: httpx.Client, mail: EmailMessage, thread_id: str | None) -> dict:
    payload = {"raw": base64.urlsafe_b64encode(mail.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    sent = (
        client.post(
            "/messages/send",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        .raise_for_status()
        .json()
    )
    return {
        "status": "ok",
        "message": f"E-posta {mail['To']} adresine gönderildi.",
        "id": sent.get("id"),
    }


def compose(to: str, subject: str, body: str) -> EmailMessage:
    mail = EmailMessage()
    mail["To"] = to
    mail["Subject"] = subject
    mail.set_content(body)
    return mail


def _reply(client: httpx.Client, message_id: str, body: str) -> dict:
    msg = (
        client.get(
            f"/messages/{message_id}",
            params={
                "format": "metadata",
                "metadataHeaders": ["From", "Reply-To", "Subject", "Message-ID", "References"],
            },
        )
        .raise_for_status()
        .json()
    )
    h = _headers(msg)
    to = h.get("reply-to") or h.get("from", "")
    subject = h.get("subject", "")
    mail = compose(to, subject if subject.lower().startswith("re:") else f"Re: {subject}", body)
    if h.get("message-id"):
        mail["In-Reply-To"] = h["message-id"]
        mail["References"] = f"{h.get('references', '')} {h['message-id']}".strip()
    return _send(client, mail, msg.get("threadId"))


async def list_emails(args: dict, settings: Settings) -> dict:
    query = (args.get("query") or "is:unread in:inbox").strip()
    limit = max(1, min(10, int(args.get("max_results") or 5)))
    return await asyncio.to_thread(_call, _list, settings, query, limit)


async def read_email(args: dict, settings: Settings) -> dict:
    if not args.get("id"):
        return {"status": "error", "message": "Hangi e-posta? Önce list_emails ile listele."}
    return await asyncio.to_thread(_call, _read, settings, str(args["id"]))


async def find_email_address(args: dict, settings: Settings) -> dict:
    name = (args.get("name") or "").strip()
    if not name:
        return {"status": "error", "message": "Kimin adresi?"}
    return await asyncio.to_thread(_call, _find_address, settings, name)


async def send_email(args: dict, settings: Settings) -> dict:
    to, subject, body = (args.get(k, "").strip() for k in ("to", "subject", "body"))
    if not EMAIL_RE.match(to):
        return {
            "status": "error",
            "message": f"'{to}' geçerli bir e-posta adresi değil. Önce find_email_address kullan.",
        }
    if not body:
        return {"status": "error", "message": "E-postanın metni boş."}
    if settings.dry_run:
        return {"status": "ok", "message": f"(deneme) E-posta {to} adresine gönderilecekti."}
    return await asyncio.to_thread(
        _call, _send, settings, compose(to, subject or "(konusuz)", body), None
    )


async def reply_email(args: dict, settings: Settings) -> dict:
    message_id, body = str(args.get("id", "")), (args.get("body") or "").strip()
    if not message_id or not body:
        return {"status": "error", "message": "Yanıt için e-posta numarası ve metin gerekli."}
    if settings.dry_run:
        return {"status": "ok", "message": "(deneme) Yanıt gönderilecekti."}
    return await asyncio.to_thread(_call, _reply, settings, message_id, body)


def _fetch(client: httpx.Client, query: str, limit: int) -> dict:
    found = (
        client.get("/messages", params={"q": query, "maxResults": limit}).raise_for_status().json()
    )
    messages = []
    for ref in found.get("messages", []):
        mail = _read(client, ref["id"])
        messages.append({k: mail[k] for k in ("id", "from", "subject", "date", "body")})
    return {"status": "ok", "untrusted_content": True, "messages": messages}


async def fetch_messages(settings: Settings, query: str, limit: int) -> dict:
    """Full text of the messages matching `query`, for the memory index."""
    return await asyncio.to_thread(_call, _fetch, settings, query, max(1, min(50, limit)))
