"""Friday's service: her own thread, the pause switch, the Gmail login, and the fetch worker. One object per app.

The loop keeps time: each minute it releases approved mail (the sender re-checks every rule), starts the daily job fetch, then
scoring and writing. Paused means nothing sends and no model runs: the thread stays idle and every gate refuses.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..assistant import brain
from . import config, mail
from . import ledger as ledger_mod
from . import home as home_mod
from .db import DonnaDB

LOOP_SECONDS = 60
WORKER_SCRIPT = Path(__file__).with_name("worker.py")
WORKER_TIMEOUT = 60
WORKER_REPLY_MAX = 4 * 1024 * 1024
PAUSE_KEY = "paused"
SPECULATIVE_A_DAY = 3          # companies from the owner's list tried each day (row 39: on in draft mode); inside the writer's 20 runs
SPECULATIVE_RETRY_DAYS = 30    # a company with no careers address is looked at again after this long
RUN_BUDGET = 40                # sealed model runs a day (PLAN-FINAL section 3); at most 20 of them score jobs (score.py)
HAIKU, SONNET = "claude-haiku-5-5", brain.SONNET
# The worker runs under sandbox-exec (macOS) or bwrap (Linux) when present: it reads only its own code and writes only in its own
# temp folder (DJ10 M1). The empty environment stays the first wall (PLAN-FINAL row 43).
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def worker_env() -> dict:
    """The worker starts with nothing: no DONNA_*, no the mail module keys, no config paths, no PATH."""
    if sys.platform == "win32":                # Python cannot start on Windows without this one variable
        import os
        return {"SystemRoot": os.environ.get("SystemRoot", "C:\\Windows")}
    return {}


class BudgetUsed(Exception):
    """Today's model runs are used up."""


class Donna:
    def __init__(self, settings, cfg: config.DonnaConfig | None = None, factories=None, db: DonnaDB | None = None,
                 python: str | None = None, clock=time.time, runner=None):
        self.s = settings
        self.cfg = cfg or config.from_settings(settings)
        self._db = db
        self._opened = False
        self.clock = clock
        self.python = python or sys.executable
        if factories is None and self.cfg.sim:
            from ..assistant import simmail
            factories = simmail.factories(settings)      # a simulation talks to the fake post office, never to a real host
        # A sealed `claude -p` run: no tools, no MCP, no settings (the mail module's run_claude). Tests pass a fake runner.
        self.runner = runner or (lambda prompt, model, effort: brain.run_claude(settings.claude_bin, prompt, model, effort, run_root=settings.run_dir))
        self._intel = self._board = self._sheets = self._learner = self._cv = self._dataset = self._jobs = self._scorer = self._router = self._writer = self._sender = None
        self._triage: threading.Thread | None = None
        self.mail = mail.DonnaMail(self.cfg, factories, self.is_paused)
        self._stop = threading.Event()
        self._wake = threading.Event()         # set to run the next pass now (after an Undo ends), instead of waiting out the minute
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.last_error = ""

    # ---- database (opened on first use, so building the app touches no file) ----
    @property
    def db(self) -> DonnaDB:
        if self._db is None:
            self._db = DonnaDB(self.cfg.db_path)
        if not self._opened:
            self._opened = True
            home_mod.first_open(self._db)
        return self._db

    def home(self) -> home_mod.Home:
        """Where the person lives: their country, places and time zone (settings; nothing assumed)."""
        return home_mod.load(self.db)

    # ---- pause: the file data/DONNA_OFF or the database flag. Either one pauses. ----
    def is_paused(self) -> bool:
        if self.cfg.off_file.exists():
            return True
        try:
            return self.db.get(PAUSE_KEY) == "1"
        except Exception:                      # a database that cannot be read pauses her: the safe side
            return True

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.cfg.off_file.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.off_file.write_text("Friday is paused. Delete this file and use the switch in the Office to start her.\n")
            self.db.put(PAUSE_KEY, "1")
        else:
            self.db.put(PAUSE_KEY, "0")
            self.cfg.off_file.unlink(missing_ok=True)
        self.db.event("pause", "paused" if paused else "resumed", self.clock)

    def model_run(self, prompt: str, model: str, effort: str) -> str:
        """The one door to a model. Paused means no run; each day has a fixed number of runs."""
        if self.is_paused():
            raise mail.Paused("Friday is paused. No model runs until you switch her on.")
        if not self.db.take(f"runs:{self.today()}", RUN_BUDGET):          # one atomic step: two threads never share the last run
            raise BudgetUsed(f"Friday used her {RUN_BUDGET} model runs for today. She continues tomorrow.")
        return self.runner(prompt, model, effort)

    def today(self) -> str:
        """The owner's local day, as YYYY-MM-DD. Every "a day" limit counts in this day (DJ11 L2)."""
        return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    @property
    def learner(self):
        if self._learner is None:
            from .learner import Learner
            self._learner = Learner(self)
        return self._learner

    @property
    def cv(self):
        if self._cv is None:
            from .cv import CV
            self._cv = CV(self)
        return self._cv

    @property
    def dataset(self):
        if self._dataset is None:
            from .dataset import Dataset
            self._dataset = Dataset(self)
        return self._dataset

    @property
    def intel(self):
        """Follow-ups, reply drafts, warm paths, learning, the weekly digest (DJ14K)."""
        if self._intel is None:
            from .intel import Intel
            self._intel = Intel(self)
        return self._intel

    @property
    def board(self):
        """The company board (DJ14I): one status per company, moved by Friday's records or by the owner."""
        if self._board is None:
            from .board import Board
            self._board = Board(self)
        return self._board

    @property
    def sheets(self):
        """Job-hunt sheets (DJ14H): read once, stored, worked from the stored copy. Nothing in the daily loop opens a sheet address."""
        if self._sheets is None:
            from .sheets import Sheets
            self._sheets = Sheets(self)
        return self._sheets

    @property
    def job_fetch(self):
        """The daily job fetch (DJ04): sources, moved boards, one copy of each job, expiry, search-by-hand cards."""
        if self._jobs is None:
            from .fetch import JobFetch
            self._jobs = JobFetch(self)
        return self._jobs

    @property
    def scorer(self):
        """Hard filters, then the sealed score (DJ05)."""
        if self._scorer is None:
            from .score import Scorer
            self._scorer = Scorer(self)
        return self._scorer

    @property
    def router(self):
        """Email to a published hiring address, or an "apply via form" card (DJ05)."""
        if self._router is None:
            from .route import Router
            self._router = Router(self)
        return self._router

    @property
    def writer(self):
        """Drafts and cover notes, with the claims check and the AI-tell lint (DJ06). Nothing sends."""
        if self._writer is None:
            from .writer import Writer
            self._writer = Writer(self)
        return self._writer

    @property
    def sender(self):
        """The one sender (DJ07): Approve with a 10-second Undo, every rule checked again at the moment of sending."""
        if self._sender is None:
            from .sender import Sender
            self._sender = Sender(self)
        return self._sender

    def triage(self) -> dict:
        """Score the live jobs, route every role that scored 70 or more, then draft for the routed roles. Nothing is sent here."""
        from .postfacts import extract
        self.scorer.refilter_locations()
        self.router.reroute_not_hiring()
        counts = self.scorer.run()
        routed = 0
        jobs = {j["id"]: j for j in self.job_fetch.store.live()}
        for row in self.scorer.list("draft"):
            if row["job_id"] in jobs and self.router.get(row["job_id"]) is None:
                self.router.route(extract(jobs[row["job_id"]]), row)
                routed += 1
        self.writer.run()
        spec = self.speculative_step()
        self.queue_drafts()
        try:
            self.board.read_replies()          # read-only look at Gmail for replies; a failure only skips this step
            self.intel.reply_drafts()
            self.intel.followups()
            self.queue_drafts()
            self.board.refresh()
        except Exception as e:  # noqa: BLE001
            self.db.event("board", f"Board refresh failed ({type(e).__name__})", self.clock)
        self.db.put("triage:last_day", self.today())
        return {**counts, "routed": routed, "speculative": spec}

    def speculative_step(self) -> int:
        """The speculative kind (PLAN-FINAL row 39): a note to a company from the owner's list that has a website but no open role that
        fits, only to a careers or contact address on its own site (`Router.speculative`). At most SPECULATIVE_A_DAY companies a day.
        Drafts only: the owner still approves each one. Returns how many companies were tried."""
        if not (self.learner.is_approved() and self.cv.ticked_count()):
            return 0
        tried = {r["company_id"]: r["ts"] for r in self.db.query("SELECT company_id, ts FROM speculative")}
        recent = self.clock() - SPECULATIVE_RETRY_DAYS * 86400
        n = 0
        from .sheets import company_priority
        for company in sorted(self.dataset.companies(), key=company_priority):     # Priority Tier first (stable)
            if not company.get("domain") or tried.get(company["id"], 0) > recent:
                continue
            if not self.db.take(f"speculative:{self.today()}", SPECULATIVE_A_DAY):
                break
            n += 1
            route = self.router.speculative(company)
            if route.get("kind") != "speculative":
                continue
            out = self.writer.write_speculative(company, route)
            if out.get("state") == "not_ready":          # no run left, or something is missing: look at this company again next time
                with self.db.tx() as c:
                    c.execute("DELETE FROM speculative WHERE company_id=?", (company["id"],))
                self.db.give_back(f"speculative:{self.today()}")
                break
        return n

    def queue_drafts(self) -> int:
        """Every waiting email draft becomes a row in the outbox ("Waiting for you"). A role already in the list is skipped. Nothing is sent."""
        n = 0
        for d in self.writer.list("waiting"):
            if d["channel"] != "email":
                continue
            r = self.sender.queue(d["id"])
            n += bool(r.get("outbox_id")) and not r.get("already")
        return n

    def triage_due(self) -> bool:
        """Once a day, after the day's fetch has finished. Never in a simulation."""
        day = self.today()
        if self.cfg.sim or self.db.get("fetch:last_run_day") != day or self.db.get("triage:last_day") == day:
            return False
        return not self.job_fetch.running and not (self._triage and self._triage.is_alive())

    def _triage_background(self) -> None:
        try:
            self.triage()
        except Exception as e:  # noqa: BLE001: one bad day must not stop the next
            self.db.event("score", f"Scoring failed ({type(e).__name__})", self.clock)

    @property
    def blocklist(self):
        from .blocklist import Blocklist
        return Blocklist(self.cfg.blocklist_file)

    # ---- the Gmail login ----
    def save_login(self, address: str, password: str) -> dict:
        """Check and save. The answer never holds the password."""
        problem = config.save_login(self.cfg.secrets_file, address, password)
        if problem:
            return {"ok": False, "error": problem}
        self.db.event("login", "Gmail login saved", self.clock)
        return {"ok": True, **self.login_status()}

    def login_status(self) -> dict:
        addr = config.address(self.cfg.secrets_file)
        return {"configured": bool(addr), "address": addr}

    def test_connection(self) -> dict:
        r = self.mail.check_login()
        self.db.event("login-test", "imap " + ("ok" if r.get("imap") == "ok" else "failed") + ", smtp " + ("ok" if r.get("smtp") == "ok" else "failed"), self.clock)
        return r

    # ---- the fetch worker ----
    def sandbox_kind(self) -> str:
        """"macos", "bwrap" or "none". With "none" only the empty environment protects the worker, and the page says so."""
        if sys.platform == "darwin" and Path(SANDBOX_EXEC).exists():
            return "macos"
        if sys.platform.startswith("linux") and shutil.which("bwrap"):
            return "bwrap"
        return "none"

    def _worker_argv(self, cwd: str | None = None) -> list[str]:
        """The worker command line. It may read only its own code and the Python install, and may write only in its own temp folder
        (DJ10 M1). Friday's data, the mail module's data, the config folders, ssh keys and the shared ledger stay closed to it."""
        argv = [self.python, "-I", str(WORKER_SCRIPT)]
        kind = self.sandbox_kind()
        home = Path(self.s.home).resolve()
        code = [WORKER_SCRIPT.resolve().parent, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                Path(self.python).resolve().parent.parent]
        secret = [Path(self.cfg.config_dir).resolve(), Path(self.cfg.data_dir).resolve(), home / ".config", home / ".ssh",
                  ledger_mod.default_dir(home).resolve(), home / "Desktop", home / "Documents", home / "Downloads"]
        if kind == "macos":
            q = lambda p: f'(subpath "{p}")'  # noqa: E731
            rules = ("(version 1)(allow default)(deny file-write*)"
                     + (f"(allow file-write* {q(Path(cwd).resolve())})" if cwd else "")
                     + '(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper"))'
                     + "(deny file-read-data " + "".join(q(p) for p in secret) + ")"
                     + "(allow file-read-data " + "".join(q(p) for p in code) + ")"
                     # A later rule wins, so the closed folders are shut again inside the open code folders.
                     + "(deny file-read-data " + "".join(q(p) for p in (secret[0], secret[1], home / ".config", home / ".ssh")) + ")")
            argv = [SANDBOX_EXEC, "-p", rules, *argv]
        elif kind == "bwrap":
            binds = ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", str(home)]
            for p in code:
                binds += ["--ro-bind-try", str(p), str(p)]
            if cwd:
                binds += ["--bind", str(Path(cwd).resolve()), str(Path(cwd).resolve())]
            for p in (secret[0], secret[1]):
                binds += ["--tmpfs", str(p)]
            argv = [shutil.which("bwrap"), *binds, "--unshare-ipc", "--unshare-pid", "--die-with-parent", "--chdir",
                    str(Path(cwd).resolve()) if cwd else "/tmp", *argv]
        return argv

    def run_worker(self, request: dict, timeout: float = WORKER_TIMEOUT, sandbox: bool = True, reply_max: int = WORKER_REPLY_MAX) -> dict:
        """One request to a fresh worker process. The worker has no secret and cannot read or write Friday's files (where the platform allows)."""
        with tempfile.TemporaryDirectory(prefix="donna-worker-") as cwd:
            argv = self._worker_argv(cwd) if sandbox else [self.python, "-I", str(WORKER_SCRIPT)]
            try:
                p = subprocess.run(argv, input=(json.dumps(request) + "\n").encode("utf-8"), capture_output=True, timeout=timeout,
                                   env=worker_env(), cwd=cwd, close_fds=True, start_new_session=True)
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "The page took too long."}
            except OSError as e:
                return {"ok": False, "error": f"The worker could not start ({type(e).__name__})."}
        try:
            out = json.loads(p.stdout[:reply_max].decode("utf-8").splitlines()[0])
            if not isinstance(out, dict):
                raise ValueError
        except (ValueError, IndexError):
            return {"ok": False, "error": "The worker gave no usable answer."}
        return out

    def fetch(self, url: str, etag: str = "", robots: bool = True) -> dict:
        """Fetch one public page through the worker. The answer is untrusted text."""
        return self.run_worker({"op": "fetch", "url": url, "user_agent": self.cfg.user_agent, "etag": etag, "robots": robots})

    # ---- the thread ----
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="donna", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self, after: float = 0.0) -> None:
        """Run the next pass soon (the loop sends approved mail whose Undo is over). Never sends by itself: the pass checks every rule."""
        if after > 0:
            t = threading.Timer(after, self._wake.set)
            t.daemon = True
            t.start()
        else:
            self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001: the loop must not die
                self.last_error = config.scrub(f"{type(e).__name__}: {e}", self.cfg.secrets_file)[:200]
            try:
                from . import status
                status.write(self)             # counts only, also while paused (DJ08)
            except Exception as e:  # noqa: BLE001: a snapshot that fails must not stop her
                self.last_error = config.scrub(f"status: {type(e).__name__}", self.cfg.secrets_file)[:200]
            if not self._stop.is_set():
                self._wake.wait(LOOP_SECONDS)
            self._wake.clear()

    def tick(self) -> None:
        """One pass. Paused: nothing runs. The sender re-checks every rule at the moment of sending (`Sender.evaluate`)."""
        if self.is_paused():
            return
        self.ticks += 1
        self.sender.release_due()          # approved mail whose Undo is over; every rule is checked again inside
        if self.job_fetch.due():
            self.job_fetch.start()
        elif self.triage_due():
            self._triage = threading.Thread(target=self._triage_background, name="donna-score", daemon=True)
            self._triage.start()

    def status(self) -> dict:
        return {"paused": self.is_paused(), "login": self.login_status(), "running": bool(self._thread and self._thread.is_alive()),
                "ticks": self.ticks, "error": self.last_error, "worker_sandbox": self.sandbox_kind()}
