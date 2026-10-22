"""Friday's daily job fetch (JOB-SOURCES 4.1). Every request and every parse runs in the DJ01 fetch worker (no secret).

- Once a day, at a quiet hour: one GET per board, 2 to 4 workers at a time, a short pause between requests, a plain User-Agent
  with a contact address, If-None-Match when the source gave an ETag.
- Boards and the sources with unverified terms (Ashby, Workable, SmartRecruiters, Recruitee) are read at most once a day, always.
  The free feeds may be read again the same day by hand ("fetch now"), inside each feed's daily call cap (Remotive: 4).
- HN "Who is hiring?": find this month's thread on the 1st to 3rd (one search a day; later in the month at most one search a
  week while it is still unknown), read it once, then read it again once a week.
- A board that answers 404 has moved: it is marked "moved", its jobs expire, and the other ATS addresses are tried for the same
  token (one GET each, stop at the first board with jobs). Netflix left Lever this way.
- LinkedIn, Indeed, Naukri, Upwork, Wellfound, YC Work at a Startup, Contra and Jooble are never read: they get search-by-hand
  cards. No ATS "submit application" address is ever called: the sources only build board-list addresses.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, sources
from .dedupe import JobStore, days_between
from .sources import hn as hn_source
from .sources import manual

FETCH_HOUR = 4                 # the quiet hour (local time): the first tick at or after it starts the day's fetch
LANES = 3                      # workers at a time (JOB-SOURCES: 2 to 4)
CHUNK = 8                      # requests per worker process
PAUSE = 1.5                    # seconds between two requests in one worker
HN_REREAD_DAYS = 7
HN_FIND_DAYS = (1, 2, 3)
REPLY_MAX = 64 * 1024 * 1024
SEARCH_TITLES: list = []             # no default: the person's titles and places come from the first-run page
DEFAULT_LOCATIONS: list = ["Remote"]
GONE = (404, 410)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_scopes (scope TEXT PRIMARY KEY, source TEXT NOT NULL, last_day TEXT NOT NULL DEFAULT '',
    last_ok_day TEXT NOT NULL DEFAULT '', etag TEXT NOT NULL DEFAULT '', status INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '', jobs INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS fetch_calls (day TEXT NOT NULL, source TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY(day, source));
"""


def board_scope(ats: str, token: str) -> str:
    return f"board:{ats}:{token}"


class JobFetch:
    def __init__(self, donna, runner=None, lanes: int = LANES, chunk: int = CHUNK, pause: float = PAUSE):
        self.d = donna
        self.store = JobStore(donna.db)
        self.runner = runner or self._run_worker
        self.lanes, self.chunk, self.pause = max(1, min(lanes, 4)), max(1, chunk), pause
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _board_url(self, mod, token: str) -> str:
        """A board that can filter by country gets the person's country code (settings), else it reads every country."""
        try:
            return mod.board_url(token, self.d.home().country_code)
        except TypeError:
            return mod.board_url(token)

    # ---- time and identity ----
    def today(self) -> str:
        return self.d.today()

    def user_agent(self) -> str:
        """The plain User-Agent with a contact address: Friday's own Gmail address (PLAN-FINAL section 4)."""
        ua = self.d.cfg.user_agent
        contact = config.address(self.d.cfg.secrets_file).strip()
        if contact and re.fullmatch(r"[^\s()<>@,;]+@[^\s()<>@,;]+\.[A-Za-z]{2,}", contact) and ua.endswith(")"):
            ua = ua[:-1] + f"; contact: {contact})"
        return ua

    # ---- worker ----
    def _run_worker(self, request: dict) -> dict:
        timeout = 30 + 50 * len(request.get("tasks") or [])
        return self.d.run_worker(request, timeout=timeout, reply_max=REPLY_MAX)

    def _send(self, tasks: list[dict], stop_on_hit: bool = False) -> list[dict]:
        """One worker request. Only id, parser, address, ETag and context go to the worker."""
        req = {"op": "jobs", "user_agent": self.user_agent(), "pause": self.pause, "stop_on_hit": stop_on_hit,
               "tasks": [{k: t[k] for k in ("id", "parser", "url", "etag", "ctx") if k in t} for t in tasks]}
        reply = self.runner(req)
        if not reply.get("ok"):
            err = str(reply.get("error") or "The worker failed.")[:200]
            return [{"id": t["id"], "ok": False, "status": 0, "error": err, "jobs": [], "meta": {}} for t in tasks]
        return [r for r in reply.get("results") or [] if isinstance(r, dict)]

    def _run_all(self, tasks: list[dict]) -> list[dict]:
        chunks = [tasks[i:i + self.chunk] for i in range(0, len(tasks), self.chunk)]
        if not chunks:
            return []
        with ThreadPoolExecutor(max_workers=self.lanes, thread_name_prefix="donna-fetch") as pool:
            return [r for rs in pool.map(self._send, chunks) for r in rs]

    # ---- ledgers ----
    def _scope(self, scope: str) -> dict:
        r = self.d.db.query("SELECT * FROM fetch_scopes WHERE scope=?", (scope,))
        return r[0] if r else {"scope": scope, "last_day": "", "last_ok_day": "", "etag": "", "status": 0, "error": "", "jobs": 0}

    def calls_today(self, source: str) -> int:
        r = self.d.db.query("SELECT n FROM fetch_calls WHERE day=? AND source=?", (self.today(), source))
        return r[0]["n"] if r else 0

    def _take(self, source: str) -> bool:
        """Count one call for today. False (and nothing counted) when the source's daily cap is used up."""
        cap = sources.module(source).MAX_CALLS
        day = self.today()
        with self.d.db.tx() as c:
            r = c.execute("SELECT n FROM fetch_calls WHERE day=? AND source=?", (day, source)).fetchone()
            n = r["n"] if r else 0
            if cap is not None and n >= cap:
                return False
            c.execute("INSERT INTO fetch_calls(day, source, n) VALUES(?, ?, 1) ON CONFLICT(day, source) DO UPDATE SET n = n + 1",
                      (day, source))
        return True

    def _task(self, scope: str, source: str, parser: str, url: str, ctx: dict, **office) -> dict:
        s = self._scope(scope)
        etag = s["etag"] if s["last_ok_day"] else ""
        return {"id": scope, "scope": scope, "source": source, "parser": parser, "url": url, "etag": etag, "ctx": ctx, **office}

    # ---- the plan ----
    def _hn_thread(self) -> str:
        v = self.d.db.get("hn:thread")
        month, _, tid = v.partition(":")
        return tid if month == self.today()[:7] and tid else ""

    def _hn_tasks(self, capped: list) -> list[dict]:
        today = self.today()
        tid = self._hn_thread()
        if tid:
            last = self.d.db.get("hn:read_day")
            if last and _days(last, today) < HN_REREAD_DAYS:
                return []
            if not self._take("hn"):
                capped.append("hn")
                return []
            r = hn_source.thread_request(tid)
            return [self._task(r["scope"], "hn", "hn", r["url"], r["ctx"])]
        last_find = self.d.db.get("hn:find_day")
        day = int(today[8:10])
        if last_find == today or (day not in HN_FIND_DAYS and last_find and _days(last_find, today) < HN_REREAD_DAYS):
            return []
        if not self._take("hn"):
            capped.append("hn")
            return []
        r = hn_source.find_request()
        return [self._task(r["scope"], "hn", "hn_find", r["url"], {"month": today[:7]})]

    def plan(self) -> tuple[list[dict], list[str]]:
        """(tasks for today, sources that hit their daily cap). Boards first, then feeds, then HN."""
        today = self.today()
        tasks, capped = [], []
        for b in self.d.dataset.boards("active"):
            if b["ats"] not in sources.ATS:
                continue
            scope = board_scope(b["ats"], b["token"])
            if self._scope(scope)["last_day"] == today:      # one GET per board per day, also when fetching by hand
                continue
            if not self._take(b["ats"]):
                capped.append(b["ats"])
                continue
            mod = sources.module(b["ats"])
            tasks.append(self._task(scope, b["ats"], b["ats"], self._board_url(mod, b["token"]), {"company": b["company"], "token": b["token"]},
                                    board=b))
        words = self.d.db.get_json("feed_keywords", [], list) or self.d.db.get_json("prefs:titles", [], list)
        opts = {"keywords": words, "country_code": self.d.home().country_code}
        for name in sources.FEEDS:
            mod = sources.module(name)
            for r in mod.requests(opts):
                if self._scope(r["scope"])["last_day"] == today:
                    continue
                if not self._take(name):
                    capped.append(name)
                    continue
                tasks.append(self._task(r["scope"], name, name, r["url"], r["ctx"]))
        tasks += self._hn_tasks(capped)
        return tasks, sorted(set(capped))

    # ---- results ----
    def _save_scope(self, t: dict, r: dict, jobs: int | None = None) -> None:
        today = self.today()
        ok = bool(r.get("ok"))
        with self.d.db.tx() as c:
            c.execute("INSERT INTO fetch_scopes(scope, source) VALUES(?, ?) ON CONFLICT(scope) DO NOTHING", (t["scope"], t["source"]))
            c.execute("UPDATE fetch_scopes SET last_day=?, status=?, error=? WHERE scope=?",
                      (today, int(r.get("status") or 0), "" if ok else str(r.get("error") or "")[:200], t["scope"]))
            if ok:
                c.execute("UPDATE fetch_scopes SET last_ok_day=?, etag=CASE WHEN ?='' THEN etag ELSE ? END WHERE scope=?",
                          (today, r.get("etag") or "", (r.get("etag") or "")[:200], t["scope"]))
            if jobs is not None:
                c.execute("UPDATE fetch_scopes SET jobs=? WHERE scope=?", (jobs, t["scope"]))

    def _apply(self, t: dict, r: dict, summary: dict) -> None:
        today = self.today()
        if not r.get("ok"):
            if r.get("blocked"):                       # the site does not want robots: note it, skip it today, move on (DJ14H)
                r = {**r, "error": "Site blocks automated visits, skipped"}
                self.d.sheets.note_blocked(re.sub(r"^https?://([^/]+).*$", r"\1", t.get("url", "")))
                summary.setdefault("blocked", []).append(t["scope"])
                self._save_scope(t, r)
                return
            self._save_scope(t, r)
            if t.get("board") and int(r.get("status") or 0) in GONE:
                summary["gone"].append(t)
            else:
                summary["errors"].append({"scope": t["scope"], "error": str(r.get("error") or "")[:200]})
            return
        summary["read"] += 1
        if r.get("unchanged"):
            self._save_scope(t, r)
            summary["unchanged"] += 1
            self.store.unchanged(t["scope"], today)
            return
        if t["parser"] == "hn_find":
            self.d.db.put("hn:find_day", today)
            tid = str((r.get("meta") or {}).get("thread_id") or "")
            if tid.isdigit():
                self.d.db.put("hn:thread", f"{today[:7]}:{tid}")
                self.d.db.put("hn:read_day", "")
                summary["hn_thread"] = tid
            self._save_scope(t, r, 0)
            return
        mine = [j for j in r.get("jobs") or [] if isinstance(j, dict) and j.get("source") == t["source"]]   # a parser speaks for its own source only
        seen = []
        for _jid, copy_id, what in self.store.add_many(mine, t["scope"], today):
            seen.append(copy_id)
            summary[what] += 1
        gone = self.store.missing(t["scope"], seen, today)
        summary["expired"] += gone["expired"]
        if t["source"] == "hn":
            self.d.db.put("hn:read_day", today)
        self._save_scope(t, r, len(seen))

    def _moved(self, t: dict, summary: dict) -> None:
        """Mark the board moved, expire its jobs, try the other ATS addresses for the same token."""
        today, b = self.today(), t["board"]
        self.store.expire_scope(t["scope"], today)
        order = [a for a in (("lever_eu",) if b["ats"] == "lever" else ()) + sources.ATS if a != b["ats"]]
        probes = []
        for ats in order:
            if self._scope(board_scope(ats, b["token"]))["last_day"] == today or not self._take(ats):
                continue
            probes.append(self._task(board_scope(ats, b["token"]), ats, ats, self._board_url(sources.module(ats), b["token"]),
                                     {"company": b["company"], "token": b["token"]}))
        hit = None
        results = {r.get("id"): r for r in (self._send(probes, stop_on_hit=True) if probes else [])}
        for p in probes:
            r = results.get(p["id"])
            if r is None:
                continue                              # not tried: an earlier address was the hit
            if r.get("ok") and int(r.get("status") or 0) == 200 and r.get("jobs"):
                hit = (p, r)
                break
            self._save_scope(p, r)
        with self.d.db.tx() as c:
            note = f"Moved: {b['ats']} answered 404 on {today}."
            if hit:
                p = hit[0]
                old = c.execute("SELECT id, state FROM boards WHERE ats=? AND token=?", (p["source"], b["token"])).fetchone()
                if old:
                    if old["state"] != "off":
                        c.execute("UPDATE boards SET state='active' WHERE id=?", (old["id"],))
                else:
                    url = sources.module(p["source"]).CAREERS.format(b["token"])
                    c.execute("INSERT INTO boards(ats, token, company, careers_url, remote_ok, india_ok, notes, state, source, added_ts) "
                              "VALUES(?, ?, ?, ?, ?, ?, ?, 'active', 'moved', ?)",
                              (p["source"], b["token"], b["company"], url, b.get("remote_ok", ""), b.get("india_ok", ""),
                               f"Found on {today}: the {b['ats']} board moved here.", self.d.clock()))
                note += f" Now on {p['source']}."
            else:
                note += " No other job board found for this name."
            notes = (b.get("notes") or "").strip()
            c.execute("UPDATE boards SET state='moved', notes=? WHERE id=?", ((notes + " " + note).strip()[:1000], b["id"]))
        summary["moved"].append({"company": b["company"], "from": b["ats"], "to": hit[0]["source"] if hit else ""})
        self.d.db.event("jobs", f"{b['company']}: the {b['ats']} board moved" + (f" to {hit[0]['source']}" if hit else ""), self.d.clock)
        if hit:
            self._apply(hit[0], hit[1], summary)

    # ---- one run ----
    def run(self) -> dict:
        """Fetch everything due today: one GET per board a day, the free feeds inside their daily caps."""
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "A fetch is already running."}
        try:
            today = self.today()
            self.d.db.put("fetch:last_run_day", today)
            tasks, capped = self.plan()
            summary = {"read": 0, "unchanged": 0, "new": 0, "seen": 0, "merged": 0, "expired": 0, "errors": [], "gone": [], "moved": [],
                       "capped": capped, "hn_thread": ""}
            by_id = {t["id"]: t for t in tasks}
            for r in self._run_all(tasks):
                t = by_id.get(r.get("id"))
                if t:
                    self._apply(t, r, summary)
            if summary["hn_thread"]:                        # found today: read it now
                follow = self._hn_tasks(summary["capped"])
                for r in (self._send(follow) if follow else []):
                    t = {x["id"]: x for x in follow}.get(r.get("id"))
                    if t:
                        self._apply(t, r, summary)
            for t in summary.pop("gone"):
                self._moved(t, summary)
            out = {"ok": True, "day": today, "requests": len(tasks), **summary, **self.store.counts()}
            self.d.db.event("jobs", f"Fetch {today}: {len(tasks)} sources, {out['new']} new jobs, {out['merged']} merged, "
                                    f"{out['expired']} expired, {len(out['errors'])} errors, {len(out['moved'])} moved", self.d.clock)
            return out
        finally:
            self._lock.release()

    # ---- the daily start ----
    def due(self) -> bool:
        """At or after the quiet hour, once a day. Never in a simulation (it would reach real hosts), and only after the owner has
        at least one active board (the seed list or the dataset is imported): before that Friday has nothing set up to search."""
        now = time.localtime(self.d.clock())
        if self.d.cfg.sim or now.tm_hour < FETCH_HOUR or self.d.db.get("fetch:last_run_day") == self.today():
            return False
        return self.d.dataset.counts()["by_state"]["active"] > 0

    def start(self) -> bool:
        """Run in the background, so Friday's own thread keeps its pace."""
        if self.running:
            return False
        self._thread = threading.Thread(target=self._background, name="donna-jobs", daemon=True)
        self._thread.start()
        return True

    def _background(self) -> None:
        try:
            self.run()
        except Exception as e:  # noqa: BLE001: one bad day must not stop the next
            self.d.db.event("jobs", f"Fetch failed ({type(e).__name__})", self.d.clock)

    # ---- for the page ----
    def search_cards(self) -> list[dict]:
        """Search-by-hand cards for the sites Friday never reads, from the owner's titles and locations."""
        def setting(key, default):
            v = self.d.db.get_json(key, None, list)
            return [str(x) for x in v] if v else default
        return manual.cards(setting("prefs:titles", SEARCH_TITLES), setting("prefs:locations", DEFAULT_LOCATIONS), self.d.home().country_code)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def _days(a: str, b: str) -> int:
    d = days_between(a, b)
    return 10_000 if d is None else d            # no readable day counts as long ago
