"""Stand-alone Friday: a small web app with only Friday's routes and pages (DJ50).

    python -m backend.donna_app        # 127.0.0.1 only; port from DONNA_PORT (default 8795)

Folders (set by the launcher; each has a default for the person's system):
    DONNA_DATA_DIR    the database, the CV, the blocklist. Outside the app folder, so an update keeps them.
    DONNA_CONFIG_DIR  the private folder with the Gmail login (owner-only permissions where the system supports them).
Friday keeps all her data on this computer. Nothing is sent anywhere except the email the person approves, the job pages she reads, and
the text she gives to the person's own `claude` program.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .api.donna import build_donna_router
from .control.guard import ConfirmStore, PageGuard, Rejected
from .donna import home as home_mod
from .donna.service import Donna
from .donna import DONNA_VERSION as VERSION

log = logging.getLogger("donna")
ROOT = Path(__file__).resolve().parents[1]
PAGES = {"styles.css", "donna.css", "donna-setup.css", "popup.js", "donna-shell.js", "donna.js", "donna-setup.js", "donna-app.js"}

SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                 b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
    (b"x-frame-options", b"DENY"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
]


def default_dirs() -> tuple[Path, Path]:
    """(data folder, private config folder) for this system when the launcher sets none."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Friday"
        return base / "data", home / ".config" / "friday"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Friday"
        return base / "data", base / "config"
    base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "friday"
    return base / "data", Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "friday"


def _find_claude() -> str:
    import shutil
    return os.environ.get("DONNA_CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


@dataclass
class DonnaSettings:
    """The few settings Friday reads from the app around her."""
    home: Path
    data_dir: Path
    run_dir: Path
    claude_bin: str
    tz: object
    port: int = 8795
    sim: bool = False
    frontend_dir: Path = field(default_factory=lambda: ROOT / "frontend")
    extra_hosts: tuple = ()


def load_settings() -> DonnaSettings:
    data, cfg = default_dirs()
    data = Path(os.environ.get("DONNA_DATA_DIR") or data)
    os.environ.setdefault("DONNA_CONFIG_DIR", str(cfg))
    os.environ.setdefault("DONNA_LEDGER_DIR", str(data / "ledger"))
    try:
        tz = ZoneInfo(os.environ["DONNA_TZ"]) if os.environ.get("DONNA_TZ") else datetime.now().astimezone().tzinfo
    except (ZoneInfoNotFoundError, ValueError):
        tz = datetime.now().astimezone().tzinfo
    return DonnaSettings(home=Path.home(), data_dir=data, run_dir=data / "run", claude_bin=_find_claude(), tz=tz,
                         port=int(os.environ.get("DONNA_PORT", "8795")),
                         frontend_dir=Path(os.environ.get("DONNA_FRONTEND_DIR") or ROOT / "frontend"))


class Control:
    """What the Friday routes need from the app around them: the page guard, one-time confirm tokens and a small log of refusals."""

    def __init__(self, settings: DonnaSettings):
        self.guard = PageGuard(settings.port, settings.extra_hosts)
        self.confirms = ConfirmStore(120.0)
        self.path = Path(settings.data_dir) / "commands.jsonl"
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        import secrets
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)

    @property
    def log(self):
        return self

    def write(self, **rec) -> None:
        keep = {k: str(v)[:200] for k, v in rec.items() if k in ("phase", "path", "http_status", "message", "origin", "host", "sec_fetch_site")}
        keep["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(keep) + "\n")


class LocalOnly:
    """Refuse any Host other than this computer's own names (stops DNS rebinding) and add the security headers."""

    def __init__(self, app, hosts):
        self.app = app
        self.hosts = {h.lower() for h in hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        host = next((v.decode("latin-1").lower() for k, v in scope.get("headers") or [] if k == b"host"), "")
        if host not in self.hosts:
            body = b'{"detail":"This page only works at http://127.0.0.1 on this computer."}'
            await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"),
                                                                                    (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg.get("headers", []), *SECURITY_HEADERS]}
            await send(msg)
        await self.app(scope, receive, send_with_headers)


class TokenRequired:
    """Every /api read needs the page token. Open: /api/health (the launcher asks it before any page exists)."""
    OPEN = ("/api/health",)

    def __init__(self, app, guard):
        self.app = app
        self.guard = guard

    async def __call__(self, scope, receive, send):
        path = scope.get("path") or ""
        if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD") or not path.startswith("/api/") or path in self.OPEN:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        q = scope.get("query_string", b"").decode("latin-1")
        token = next((p.split("=", 1)[1] for p in q.split("&") if p.startswith("token=")), None)
        try:
            self.guard.check(headers, unquote(token) if token else None)
        except Rejected as e:
            body = json.dumps({"detail": e.message}).encode()
            await send({"type": "http.response.start", "status": e.status, "headers": [(b"content-type", b"application/json"),
                                                                                         (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def setup_done(donna: Donna) -> bool:
    """The first-run page has nothing left to ask: login, a ticked CV fact, where the person lives, titles, an approved profile."""
    try:
        h = home_mod.load(donna.db)
        titles = donna.db.get_json("prefs:titles", [], list)
        return bool(donna.login_status()["configured"] and donna.cv.page_data()["ticked"] > 0 and h.country and titles
                    and donna.learner.page_data().get("approved"))
    except Exception:  # noqa: BLE001: a page that cannot tell shows the first-run page
        return False


def claude_check(claude_bin: str, timeout: int = 120) -> dict:
    """Does `claude -p` answer, signed in? One tiny run with no tools. A plain sentence either way."""
    try:
        r = subprocess.run([claude_bin, "-p", "Reply with the single word: ready"], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"ok": False, "message": "Friday cannot find the claude program. Install Claude Code from https://docs.claude.com/en/docs/claude-code, sign in once, then try again."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Claude did not answer in time. Open a terminal, type claude, sign in, then try again."}
    except OSError:
        return {"ok": False, "message": "Friday could not start the claude program."}
    if r.returncode != 0 or not (r.stdout or "").strip():
        return {"ok": False, "message": "Claude did not answer. Open a terminal, type claude and sign in, then try again."}
    return {"ok": True, "message": "Claude answered. Friday can use your Claude Code."}


def create_app(settings: DonnaSettings | None = None, background: bool = True, donna: Donna | None = None) -> FastAPI:
    settings = settings or load_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    control = Control(settings)
    donna = donna or Donna(settings)
    h = home_mod.load(donna.db)
    if h.tz:
        try:
            settings.tz = ZoneInfo(h.tz)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    if not donna.db.get("shell_started"):          # a new install starts switched off, until the first-run page ends
        donna.db.put("shell_started", "1")
        donna.set_paused(True)

    @asynccontextmanager
    async def lifespan(app):
        if background:
            donna.start()
        yield
        donna.stop()

    app = FastAPI(title="Friday", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TokenRequired, guard=control.guard)
    app.add_middleware(LocalOnly, hosts=control.guard.hosts)
    app.state.donna = donna
    app.state.settings = settings
    app.include_router(build_donna_router(control, donna))
    frontend = Path(settings.frontend_dir)

    @app.get("/api/health")
    def health():
        return {"ok": True, "app": "friday", "version": VERSION, "setup_done": setup_done(donna)}

    @app.post("/api/shell/claude-check")
    async def check_claude(request: Request):
        try:
            control.guard.check(request.headers)
        except Rejected as e:
            raise HTTPException(e.status, e.message)
        return await asyncio.get_running_loop().run_in_executor(None, claude_check, settings.claude_bin)

    def page(name: str) -> HTMLResponse:
        html = (frontend / name).read_text(encoding="utf-8")
        i = html.lower().find("<head>")
        html = html[: i + 6] + control.guard.meta_tag() + html[i + 6:] if i >= 0 else control.guard.meta_tag() + html
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/", include_in_schema=False)
    def index():
        return page("donna-app.html") if setup_done(donna) else RedirectResponse("/setup", status_code=302)

    @app.get("/setup", include_in_schema=False)
    def setup():
        return page("donna-setup.html")

    @app.get("/{name}", include_in_schema=False)
    def static(name: str):
        if name not in PAGES or not (frontend / name).is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(frontend / name, headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn
    s = load_settings()
    uvicorn.run(create_app(s), host="127.0.0.1", port=s.port, log_level="warning")


if __name__ == "__main__":
    main()
