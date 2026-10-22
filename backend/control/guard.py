"""Safety gates for commands.

- A random page token, made once per server start. The page gets it in a <meta> tag and
  sends it back in the X-Bots-Token header (or ?token= for EventSource, which cannot set headers).
- Host and Origin checks, so other sites (and DNS rebinding) cannot call the API.
- One-time confirm tokens for stop, pause and queue changes. A token is tied to the exact
  action, target and text that the page was shown, and it expires.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field

TOKEN_HEADER = "X-Bots-Token"
TOKEN_META = "bots-token"


class Rejected(Exception):
    """A command was refused. `status` is the HTTP status, `message` is plain text for the page."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class PageGuard:
    def __init__(self, port: int, extra_hosts=()):
        self.token = secrets.token_urlsafe(32)
        names = ["127.0.0.1", "localhost"]
        self.hosts = {f"{n}:{port}" for n in names} | set(names) | set(extra_hosts)
        self.origins = {f"http://{n}:{port}" for n in names} | {f"http://{h}" for h in extra_hosts}

    def meta_tag(self) -> str:
        return f'<meta name="{TOKEN_META}" content="{self.token}">'

    def check(self, headers, query_token: str | None = None) -> None:
        host = (headers.get("host") or "").lower()
        if host not in self.hosts:
            raise Rejected(403, "Commands work only from this computer's own page.")
        origin = headers.get("origin")
        if origin and origin.lower() not in self.origins:
            raise Rejected(403, "Commands from other websites are blocked.")
        if (headers.get("sec-fetch-site") or "").lower() == "cross-site":
            raise Rejected(403, "Commands from other websites are blocked.")
        given = headers.get(TOKEN_HEADER.lower()) or query_token or ""
        if not given:
            raise Rejected(403, "The page token is missing. Reload the page and try again.")
        if not hmac.compare_digest(given.encode(), self.token.encode()):
            raise Rejected(403, "The page token is wrong. Reload the page and try again.")


def check_hook(headers, host_ok, token: str) -> None:
    """The PermissionRequest hook is not a web page, so it has no page token. It sends its own stable token. That
    token can only create a request on the security desk."""
    host = (headers.get("host") or "").lower()
    if host not in host_ok:
        raise Rejected(403, "Hook requests work only from this computer.")
    if headers.get("origin") or (headers.get("sec-fetch-site") or "").lower() == "cross-site":
        raise Rejected(403, "Hook requests do not come from web pages.")
    given = headers.get("x-bots-hook-token") or ""
    if not token or not given or not hmac.compare_digest(given.encode(), token.encode()):
        raise Rejected(403, "The hook token is missing or wrong.")


@dataclass
class Pending:
    command_id: str
    action: str
    target: dict
    text: str
    description: str
    plan: dict
    expires: float
    options: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)


class ConfirmStore:
    def __init__(self, ttl: float = 120.0, clock=time.time):
        self.ttl = ttl
        self.clock = clock
        self._items: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def add(self, command_id: str, action: str, target: dict, text: str, description: str, plan: dict,
            options: dict | None = None) -> tuple[str, Pending]:
        token = secrets.token_urlsafe(18)
        p = Pending(command_id, action, target, text, description, plan, self.clock() + self.ttl, dict(options or {}))
        with self._lock:
            now = self.clock()
            self._items = {k: v for k, v in self._items.items() if v.expires > now}
            self._items[token] = p
        return token, p

    def take(self, token: str, action: str, target: dict, text: str, options: dict | None = None) -> Pending:
        """Use a confirm token once. It must match what was shown."""
        with self._lock:
            p = self._items.pop(token, None)
        if p is None:
            raise Rejected(409, "This confirmation is not valid. It may be used already. Please start again.")
        if p.expires < self.clock():
            raise Rejected(409, "This confirmation has expired. Please start again.")
        if p.action != action or _norm(p.target) != _norm(target) or (p.text or "") != (text or "") \
                or p.options != dict(options or {}):
            raise Rejected(409, "This confirmation was for a different command. Please start again.")
        return p


def _norm(target: dict) -> tuple:
    return (str(target.get("type")), str(target.get("id")))
