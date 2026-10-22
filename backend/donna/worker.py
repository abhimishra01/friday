"""Friday's fetch worker. The only code that opens a web page for her.

It runs as its own process with a clean environment (no password, no key, no config path) and exchanges one JSON line over a pipe:
a request on stdin, an answer on stdout. Standard library only; it imports nothing from The Office, so `python -I worker.py` works
from any folder. The network rule (task README rule 9) is applied here and nowhere else:

- http and https only, ports 80 and 443 only, no login in the address.
- The host is resolved on every hop. If ANY address is loopback, private, link-local, multicast, reserved or unspecified, the hop is refused.
  The connection then goes to the address that was checked, not to a second lookup.
- At most 3 redirects, followed by hand, each one checked again.
- An honest User-Agent (given by the caller) and robots.txt on every origin.
- The answer has a size limit, no compression, and a total time limit.

Whatever comes back is untrusted text. The caller must never obey it.
"""
from __future__ import annotations

import http.client
import importlib.util
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

MAX_REDIRECTS = 3
MAX_BODY = 2 * 1024 * 1024
MAX_ROBOTS = 512 * 1024
SOCKET_TIMEOUT = 15
TOTAL_SECONDS = 40
REDIRECTS = (301, 302, 303, 307, 308)
PRODUCT = "DonnaJobSearch"
JOBS_MAX_BODY = 8 * 1024 * 1024      # a big company board with full descriptions
MAX_TASKS = 40
MAX_PAUSE = 5.0

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


# Sites Donna never reads by machine (PLAN-FINAL row 34: "search by hand" cards only). One list, checked on every hop, redirects
# included, before the host is resolved. robots.txt of these sites is not fetched either (DJ10 H1). `route.py` imports this list.
NEVER_FETCH = ("linkedin.com", "lnkd.in", "naukri.com", "upwork.com", "wellfound.com", "angel.co", "workatastartup.com",
               "contra.com", "jooble.org", "indeed.com")
# A brand word as a whole label also covers its country domains (the country domains of Indeed and LinkedIn).
NEVER_FETCH_LABELS = ("linkedin", "indeed", "naukri", "jooble")


def never_fetch(host: str) -> bool:
    h = (host or "").lower().strip(".")
    return any(h == d or h.endswith("." + d) for d in NEVER_FETCH) or any(label in NEVER_FETCH_LABELS for label in h.split("."))


class Refused(Exception):
    """A plain sentence for the page. Nothing was fetched from the refused address."""


# ---- the network rule ----

def ip_blocked(ip) -> bool:
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip.split("%")[0])
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return ip_blocked(ip.ipv4_mapped)
        if ip in _NAT64:
            return ip_blocked(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in _SIX_TO_FOUR:
            return ip_blocked(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    return (not ip.is_global) or ip.is_multicast or ip.is_loopback or ip.is_private or ip.is_link_local \
        or ip.is_reserved or ip.is_unspecified or (ip.version == 6 and ip.is_site_local)


def check_url(url: str, resolve=socket.getaddrinfo) -> tuple[str, str, int, str, list[str]]:
    """(scheme, host, port, path-with-query, checked addresses) or Refused."""
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise Refused("The address is not a valid web address.")
    if u.scheme not in ("http", "https"):
        raise Refused("Only http and https addresses are allowed.")
    if not u.hostname:
        raise Refused("The address has no host name.")
    if u.username is not None or u.password is not None:
        raise Refused("An address with a login in it is not allowed.")
    if port not in (None, 80, 443):
        raise Refused("Only the normal web ports (80 and 443) are allowed.")
    if never_fetch(u.hostname):
        raise Refused("That site is on Friday's search-by-hand list. She never reads it.")
    port = port or (443 if u.scheme == "https" else 80)
    try:
        infos = resolve(u.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise Refused("The host name could not be found.")
    ips = []
    for info in infos:
        ip = info[4][0]
        try:
            blocked = ip_blocked(ip)
        except ValueError:
            blocked = True
        if blocked:
            raise Refused("That address points to a private or local computer. It is not allowed.")
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise Refused("The host name could not be found.")
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    return u.scheme, u.hostname, port, path, ips


# ---- the real connection (replaced by a fake in tests) ----

class _Pinned(http.client.HTTPConnection):
    """Connects to the address that was checked. The Host header still carries the name."""

    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def open_once(scheme, host, port, ips, path, headers, limit, timeout=SOCKET_TIMEOUT):
    """One GET. Returns (status, headers with lower-case names, body bytes cut at `limit`, whether it was cut)."""
    last = None
    for ip in ips:
        conn = (_PinnedTLS if scheme == "https" else _Pinned)(host, port, ip, timeout)
        try:
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            body = r.read(limit + 1)
            return r.status, {k.lower(): v for k, v in r.getheaders()}, body[:limit], len(body) > limit
        except (OSError, http.client.HTTPException) as e:
            last = e
        finally:
            conn.close()
    raise Refused(f"The page could not be reached ({type(last).__name__}).")


# ---- one fetch ----

class Fetcher:
    def __init__(self, user_agent: str, resolve=socket.getaddrinfo, opener=open_once, clock=time.monotonic, total=TOTAL_SECONDS,
                 robots: dict | None = None):
        self.ua, self.resolve, self.opener, self.clock = user_agent, resolve, opener, clock
        self.deadline = clock() + total
        self.robots: dict[str, RobotFileParser | None] = {} if robots is None else robots
        self.requests = 0

    def _walk(self, url: str, limit: int, use_robots: bool, etag: str = "") -> tuple[int, dict, bytes, bool, str]:
        """GET with the redirects followed by hand. Every hop is resolved and checked again, and robots.txt is read for every origin
        the walk lands on. Returns (status, headers, body, cut, final address)."""
        for hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, path, ips = check_url(url, self.resolve)       # a private address is refused before any request
            if use_robots and not self._robots_ok(url):
                raise Refused("The site's robots.txt asks robots to stay away from this page.")
            if self.clock() > self.deadline:
                raise Refused("The page took too long.")
            headers = {"User-Agent": self.ua, "Accept": "text/html,application/json,application/xml,text/plain;q=0.8,*/*;q=0.5",
                       "Accept-Encoding": "identity", "Connection": "close"}
            if etag and hop == 0:
                headers["If-None-Match"] = etag
            self.requests += 1
            status, h, body, cut = self.opener(scheme, host, port, ips, path, headers, limit)
            if status in REDIRECTS and h.get("location"):
                if hop == MAX_REDIRECTS:
                    raise Refused("The page sent too many redirects.")
                url = urljoin(url, h["location"])
                continue
            return status, h, body, cut, url
        raise Refused("The page sent too many redirects.")

    def _robots_ok(self, url: str) -> bool:
        u = urlsplit(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in self.robots:
            rp = None
            try:
                status, _h, body, _cut, _final = self._walk(origin + "/robots.txt", MAX_ROBOTS, use_robots=False)
                if 200 <= status < 300 or 400 <= status < 500:       # 4xx: there is no robots.txt, so everything is open
                    rp = RobotFileParser()
                    rp.parse(body.decode("utf-8", "replace").splitlines() if status < 300 else [])
            except Refused:
                rp = None                                           # cannot be read (or 5xx): stay out, as RFC 9309 says
            self.robots[origin] = rp
        rp = self.robots[origin]
        return bool(rp and rp.can_fetch(PRODUCT, url))

    def fetch(self, url: str, use_robots: bool = True, etag: str = "", limit: int = MAX_BODY) -> dict:
        status, h, body, cut, final = self._walk(url, limit, use_robots, etag)
        ctype = h.get("content-type", "")
        charset = "utf-8"
        if "charset=" in ctype.lower():
            charset = ctype.lower().split("charset=", 1)[1].split(";")[0].strip(" \"'") or "utf-8"
        try:
            text = body.decode(charset, "replace")
        except LookupError:
            text = body.decode("utf-8", "replace")
        return {"ok": True, "status": status, "final_url": final, "content_type": ctype[:200], "etag": h.get("etag", "")[:200],
                "truncated": cut, "body": text, "requests": self.requests}


# ---- job sources (DJ04): fetch and parse in this process, so the Office only ever sees plain fields ----

_SOURCES = None


def sources():
    """The `sources` package next to this file, loaded by path (under `python -I` the script folder is not on sys.path)."""
    global _SOURCES
    if _SOURCES is None:
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
        spec = importlib.util.spec_from_file_location("donna_sources", os.path.join(here, "__init__.py"), submodule_search_locations=[here])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["donna_sources"] = mod
        spec.loader.exec_module(mod)
        _SOURCES = mod
    return _SOURCES


BLOCK_STATUS = (401, 403, 429)
_BOT_TITLE = re.compile(r"<title[^>]*>[^<]*(just a moment|attention required|access denied|captcha|are you a (?:robot|human)|"
                        r"verify you are|security check|bot check|unusual traffic)", re.I)


def bot_check(body: str) -> bool:
    """A small page that is a bot-check wall, not content."""
    return len(body or "") < 40_000 and bool(_BOT_TITLE.search(body or ""))


def run_task(task: dict, ua: str, robots: dict, resolve, opener) -> dict:
    """One GET and one parse. The answer holds plain job fields, the status and the ETag; never the raw page."""
    out = {"id": str(task.get("id") or "")[:200], "ok": False, "status": 0, "etag": "", "jobs": [], "meta": {}}
    name = str(task.get("parser") or "")
    try:
        parse = sources().parser(name)
    except (KeyError, ImportError):
        return {**out, "error": "Unknown source."}
    try:
        r = Fetcher(ua, resolve, opener, robots=robots).fetch(str(task.get("url") or ""), True, str(task.get("etag") or ""), JOBS_MAX_BODY)
    except Refused as e:
        return {**out, "error": str(e), "refused": True, "blocked": "robots.txt" in str(e)}
    except Exception as e:  # noqa: BLE001: the type only, never the text
        return {**out, "error": f"The page could not be read ({type(e).__name__})."}
    if r["status"] in BLOCK_STATUS or (r["status"] == 200 and bot_check(r["body"])):
        # A site that does not want robots: skip it. No retry, no trick (DJ14H).
        return {**out, "status": r["status"], "error": "Site blocks automated visits, skipped", "blocked": True}
    out.update(status=r["status"], etag=r["etag"], final_url=str(r.get("final_url") or "")[:1000])
    if r["status"] == 304:
        return {**out, "ok": True, "unchanged": True}
    if r["status"] != 200:
        return {**out, "error": f"The site answered {r['status']}."}
    if r["truncated"]:
        return {**out, "error": "The answer was too large to read."}
    ctx = task.get("ctx") if isinstance(task.get("ctx"), dict) else {}
    if name == "careers":
        ctx = {**ctx, "url": out["final_url"]}      # links on the page are read against the address that answered
    try:
        parsed = parse(r["body"], ctx)
    except Exception as e:  # noqa: BLE001: a page that is not what we expect
        return {**out, "error": f"The answer could not be read ({type(e).__name__})."}
    return {**out, "ok": True, "jobs": parsed.get("jobs") or [], "meta": parsed.get("meta") or {}}


def run_jobs(req: dict, resolve=socket.getaddrinfo, opener=open_once, sleep=time.sleep) -> dict:
    """Tasks one after the other, with a short pause between them. robots.txt is read once per origin for the whole list.
    With `stop_on_hit`, the list stops at the first task that answers 200 with at least one job (the moved-board search)."""
    ua = str(req.get("user_agent") or "")[:300]
    if not ua:
        return {"ok": False, "error": "A fetch needs an honest User-Agent."}
    tasks = [t for t in (req.get("tasks") or []) if isinstance(t, dict)][:MAX_TASKS]
    try:
        pause = min(max(float(req.get("pause") or 0), 0.0), MAX_PAUSE)
    except (TypeError, ValueError):
        pause = 0.0
    robots: dict = {}
    results = []
    for i, t in enumerate(tasks):
        if i and pause:
            sleep(pause)
        r = run_task(t, ua, robots, resolve, opener)
        results.append(r)
        if req.get("stop_on_hit") and r["ok"] and r["status"] == 200 and r["jobs"]:
            break
    return {"ok": True, "results": results}


def handle(req: dict, resolve=socket.getaddrinfo, opener=open_once) -> dict:
    op = req.get("op")
    if op == "jobs":
        return run_jobs(req, resolve, opener)
    if op == "selfcheck":
        # For the tests: what this process can see. Names only, never values (DJ11 K11).
        return {"ok": True, "env_keys": sorted(os.environ), "cwd": os.getcwd(), "pid": os.getpid()}
    if op == "fetch":
        ua = str(req.get("user_agent") or "")[:300]
        if not ua:
            return {"ok": False, "error": "A fetch needs an honest User-Agent."}
        try:
            return Fetcher(ua, resolve, opener).fetch(str(req.get("url") or ""), bool(req.get("robots", True)), str(req.get("etag") or ""))
        except Refused as e:
            return {"ok": False, "error": str(e), "refused": True}
        except Exception as e:  # noqa: BLE001: one bad page must not kill the worker; the type only, never the text
            return {"ok": False, "error": f"The page could not be read ({type(e).__name__})."}
    return {"ok": False, "error": "Unknown request."}


def main() -> int:
    raw = sys.stdin.buffer.readline(1024 * 1024)
    try:
        req = json.loads(raw.decode("utf-8"))
        if not isinstance(req, dict):
            raise ValueError
    except ValueError:
        out = {"ok": False, "error": "The request was not JSON."}
    else:
        out = handle(req)
    sys.stdout.buffer.write(json.dumps(out).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
