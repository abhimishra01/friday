"""Friday's sender. `Sender.send()` is the one function that can send her email. Every rule is checked here, in code, at the moment
of sending, whatever the writer or the page said. Nothing else in `backend/Friday/` calls `mailio.deliver` (a test reads the source).
It follows the pattern of the mail module's `assistant/sender.py`, which this file does not import or change.

Draft mode only (v1): every email waits for the owner. The steps of one email:
  queue(draft)   a waiting email draft becomes an outbox row. Recipient and company come from the DJ05 route, never from the text.
  approve(row)   starts a 10-second Undo. The CV file is read and its sha256 is stored.
  undo(row)      within the 10 seconds, the row goes back to waiting.
  release_due()  (the tick) sends rows whose Undo is over: `send(row)` re-checks everything first.

Order of the checks `evaluate()` runs (the word is what happens when one fails):
 1. Friday is paused (file `data/DONNA_OFF` or the flag)                                   -> held
 2. the kind is `application` or `speculative` (`freelance_pitch` is off in v1); one plain
    recipient, no CC or BCC; a clean subject                                              -> refused
 3. the recipient is the one on the route in the database                                 -> refused
 4. blocklist (`data/Friday/blocklist.txt`): recipient, company domain                     -> refused
 5. suppression: Friday's own list, or any agent's `suppressed` in the ledger              -> refused
 6. never the same role twice (her sent mail, and the "applied" list)                      -> refused
 7. at most 1 email per company in 60 days; at most 2 roles per company in 90 days        -> refused
 8. another agent used the domain in the last 90 days (ledger)                            -> needs the owner's OK (a tick on Approve)
 9. text: no placeholder, no app password, no other address                               -> held
10. the CV is the only attachment, read with the mail module's attachment checks; its sha256 is the one
    stored at Approve                                                                      -> held (approval cleared)
11. caps: 10 a day, 3 an hour by default (a page setting)                                                             -> queued (waits, stays approved)
12. weekdays 09:00 to 17:00 in the recipient's time                                        -> queued (waits, stays approved)
Then: the ledger domain is RESERVED, then the mail is delivered. A mail host that was never reached (`ConnectFailed`) gives the
reservation back and tries again after 5 minutes, for 6 hours. Any other error after the hand-over is never retried (no double send).
A row is claimed ('sending') before delivery, so two callers cannot send it twice. A row found 'sending' at start-up goes to 'held'.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta
from email.utils import getaddresses
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..assistant import mailio, placeholders
from ..assistant.db import EMAIL_OK, norm_email
from . import blocklist as blocklist_mod
from . import config, ledger as ledger_mod
from .ledger import Ledger, LedgerError, LedgerRefused
from .domains import host_of, registrable
from .route import is_hiring_system

KINDS = ("application", "speculative", "followup", "reply")          # `freelance_pitch` is refused in v1 (PLAN-FINAL row 39)
UNDO_SECONDS = 10
CAP_DAY, CAP_HOUR = 10, 3                       # the defaults; the owner can change them on the page (DJ08), up to the ceiling
MAX_CAP_DAY, MAX_CAP_HOUR = 25, 6
WINDOW_FROM, WINDOW_TO = 9, 17                  # weekdays, recipient time
COMPANY_DAYS, COMPANY_MAX_ROLES, ROLES_DAYS = 60, 2, 90
RETRY_AFTER, RETRY_WINDOW = 300, 6 * 3600
DEFAULT_ZONE = "America/New_York"          # when nothing says where the recipient is (setting `prefs:recipient_tz` changes it)
OPEN = ("waiting", "held", "undo", "queued", "sending")
APPROVABLE = ("waiting", "held")                 # Approve starts the Undo from these states
REJECTABLE = ("waiting", "held", "undo", "queued")

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL, kind TEXT NOT NULL, job_id INTEGER,
    company_id INTEGER, company TEXT NOT NULL DEFAULT '', company_norm TEXT NOT NULL DEFAULT '', title_norm TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL, to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '{}', cv_sha TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    ok_other_agent INTEGER NOT NULL DEFAULT 0, approved_ts REAL, undo_until REAL, retry_at REAL, first_fail REAL,
    created REAL NOT NULL, decided REAL, sent_ts REAL, message_id TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS outbox_state ON outbox(state);
CREATE INDEX IF NOT EXISTS outbox_job ON outbox(job_id);
CREATE TABLE IF NOT EXISTS suppressed (domain TEXT PRIMARY KEY, reason TEXT NOT NULL, ts REAL NOT NULL);
"""
# States: waiting (for the owner), held (a check stopped it; approval cleared; Approve again), undo (approved, 10 seconds),
# queued (approved and past Undo; waits for a cap, the window or a retry), sending, sent, refused (a rule says never), failed, rejected.

_ADDR_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Where the recipient is, from the words of the job's location. The earliest match in the text wins.
_ZONE_WORDS = [
    (r"\bindia\b|bengaluru|bangalore|mumbai|delhi|hyderabad|pune|chennai|noida|gurgaon|gurugram", "Asia/Kolkata"),
    (r"\bunited kingdom\b|\buk\b|london|manchester|edinburgh|\bireland\b|dublin|lisbon|portugal", "Europe/London"),
    (r"germany|berlin|munich|hamburg|france|paris|netherlands|amsterdam|spain|madrid|barcelona|italy|milan|poland|warsaw|"
     r"sweden|stockholm|denmark|copenhagen|switzerland|zurich|austria|vienna|\beurope\b|\bemea\b|\beu\b|\bcet\b", "Europe/Berlin"),
    (r"israel|tel aviv", "Asia/Jerusalem"),
    (r"dubai|\buae\b|abu dhabi", "Asia/Dubai"),
    (r"singapore|\bapac\b|malaysia|kuala lumpur|philippines|manila", "Asia/Singapore"),
    (r"japan|tokyo", "Asia/Tokyo"),
    (r"australia|sydney|melbourne|\baest\b|new zealand|auckland", "Australia/Sydney"),
    (r"san francisco|bay area|silicon valley|seattle|portland|los angeles|california|\bpacific\b|\bpst\b|\bpt\b|vancouver", "America/Los_Angeles"),
    (r"denver|salt lake|phoenix|\bmountain\b|\bmst\b", "America/Denver"),
    (r"chicago|austin|dallas|houston|texas|minneapolis|\bcentral\b|\bcst\b|mexico", "America/Chicago"),
    (r"new york|boston|miami|atlanta|washington|philadelphia|toronto|montreal|canada|\beastern\b|\best\b|\bet\b|"
     r"united states|\busa\b|\bus\b|\bu\.s\.", "America/New_York"),
]
_ZONE_RX = [(re.compile(p, re.I), z) for p, z in _ZONE_WORDS]
_TLD_ZONES = {"in": "Asia/Kolkata", "uk": "Europe/London", "ie": "Europe/London", "de": "Europe/Berlin", "fr": "Europe/Berlin",
              "nl": "Europe/Berlin", "es": "Europe/Berlin", "it": "Europe/Berlin", "se": "Europe/Berlin", "ch": "Europe/Berlin",
              "pl": "Europe/Berlin", "dk": "Europe/Berlin", "il": "Asia/Jerusalem", "ae": "Asia/Dubai", "sg": "Asia/Singapore",
              "jp": "Asia/Tokyo", "au": "Australia/Sydney", "nz": "Australia/Sydney", "ca": "America/New_York",
              "us": "America/New_York"}


def caps(db) -> tuple[int, int]:
    """(per day, per hour) from the owner's settings `cap:day` and `cap:hour`, never above the ceiling, never below 0."""
    def one(key: str, default: int, top: int) -> int:
        try:
            return max(0, min(top, int(db.get(key, "") or default)))
        except ValueError:
            return default
    return one("cap:day", CAP_DAY, MAX_CAP_DAY), one("cap:hour", CAP_HOUR, MAX_CAP_HOUR)


class Problem(NamedTuple):
    """What stopped a send, and where the row goes: refuse -> state `refused` (never), hold -> `held` (approval cleared),
    wait -> `queued` (a cap or the window; stays approved), ok_needed -> Approve answers `needs_ok` (the owner may say OK)."""
    kind: str          # refuse | hold | wait | ok_needed
    code: str
    text: str


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Sender:
    def __init__(self, donna, ledger_dir: Path | None = None):
        self.d = donna
        self.lock = threading.RLock()
        self.ledger = Ledger(ledger_dir or ledger_mod.default_dir(donna.s.home), donna.s.tz)
        self.recover_sending()

    # ---- small helpers ----
    def now(self) -> float:
        return self.d.clock()

    def _local(self, ts: float, zone=None) -> datetime:
        return datetime.fromtimestamp(ts, zone or self.d.s.tz)

    def _row(self, oid: int) -> dict | None:
        rows = self.d.db.query("SELECT * FROM outbox WHERE id=?", (int(oid),))
        return rows[0] if rows else None

    def _set(self, oid: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.d.db.tx() as c:
            c.execute(f"UPDATE outbox SET {cols} WHERE id=?", (*fields.values(), int(oid)))

    def _event(self, kind: str, r: dict, text: str) -> None:
        self.d.db.event(kind, config.scrub(f"{r.get('company') or r['domain']}: {r['subject'][:100]}. {text}".strip(), self.d.cfg.secrets_file),
                        self.d.clock)

    def _result(self, r: dict | None, status: str | None = None, reason: str | None = None, **extra) -> dict:
        r = r or {}
        return {"status": status or r.get("state", "rejected"), "outbox_id": r.get("id"),
                "reason": r.get("reason", "") if reason is None else reason, **extra}

    def get(self, oid: int) -> dict | None:
        r = self._row(oid)
        if r:
            r["source"] = json.loads(r["source"] or "{}")
        return r

    def list(self, *states: str) -> list[dict]:
        marks = ",".join("?" * len(states)) if states else ""
        rows = self.d.db.query("SELECT id FROM outbox" + (f" WHERE state IN ({marks})" if states else "") + " ORDER BY id", states)
        return [self.get(r["id"]) for r in rows]

    # ---- where the recipient is, and the sending window ----
    def recipient_zone(self, r: dict) -> ZoneInfo:
        names = [self.d.db.get(f"tz:{r['domain']}", "")]
        best = None
        for rx, zone in _ZONE_RX:
            m = rx.search(r.get("location") or "")
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), zone)
        if best:
            names.append(best[1])
        names.append(_TLD_ZONES.get(host_of("https://" + r["to_addr"].rsplit("@", 1)[-1]).rsplit(".", 1)[-1], ""))
        names.append(self.d.db.get("prefs:recipient_tz", "") or DEFAULT_ZONE)
        for n in names:
            if n:
                try:
                    return ZoneInfo(n)
                except (ZoneInfoNotFoundError, ValueError):
                    continue
        return ZoneInfo("UTC")

    def in_window(self, r: dict, now: float) -> bool:
        t = self._local(now, self.recipient_zone(r))
        return t.weekday() < 5 and WINDOW_FROM <= t.hour < WINDOW_TO

    # ---- counts ----
    def sent_since(self, ts: float) -> int:
        return self.d.db.query("SELECT COUNT(*) AS n FROM outbox WHERE state='sent' AND sent_ts>=?", (ts,))[0]["n"]

    def sent_today(self, now: float | None = None) -> int:
        now = self.now() if now is None else now
        return self.sent_since(self._local(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    # ---- the route: the only source of the recipient ----
    def _route_for(self, r: dict) -> dict | None:
        if r["kind"] == "application" and r["job_id"] is not None:
            return self.d.router.get(r["job_id"])
        if r["kind"] == "speculative" and r["company_id"] is not None:
            rows = self.d.db.query("SELECT route FROM speculative WHERE company_id=?", (r["company_id"],))
            return json.loads(rows[0]["route"]) if rows else None
        if r["kind"] in ("followup", "reply"):
            # A follow-up or a reply goes only to the address the first mail went to (a reply may go to another person at the same
            # company site). The first mail must have been sent.
            src = r.get("source") or {}
            src = json.loads(src) if isinstance(src, str) else src
            o = self._row(int(src.get("origin_outbox") or 0)) if str(src.get("origin_outbox") or "").isdigit() else None
            if not o or o["state"] != "sent":
                return None
            to = o["to_addr"]                      # a follow-up goes to the first mail's address only (DJ14KF L1)
            if r["kind"] == "reply":
                to = src.get("reply_to") or o["to_addr"]
                if norm_email(to) != norm_email(o["to_addr"]):
                    from .route import WEBMAIL
                    origin = o["to_addr"].rsplit("@", 1)[-1].lower()
                    if origin in WEBMAIL or registrable(origin) in WEBMAIL or is_hiring_system(origin):
                        return None            # a free-mail or shared first address: the reply may go to that address only (L2)
                    if registrable(to.rsplit("@", 1)[-1]) != registrable(origin):
                        return None
                    # the draft source is not trusted: the address must be one that wrote in this thread
                    if not any(norm_email(x["from_addr"]) == norm_email(to) for x in
                               self.d.db.query("SELECT from_addr FROM company_replies WHERE outbox_id=?", (o["id"],))):
                        return None
            return {"kind": r["kind"], "to": to, "company_domain": o["domain"]}
        return None

    @staticmethod
    def _plain_address(to) -> bool:
        return isinstance(to, str) and not re.search(r"[,;\r\n<>\s]", to.strip()) and len(getaddresses([to])) == 1 \
            and bool(EMAIL_OK.match(to.strip()))

    # ---- the CV: the only attachment ----
    def _cv(self) -> tuple[tuple[bytes, str] | None, Problem | None]:
        cv = self.d.cv
        meta = cv.meta()
        if not meta or not cv.path.exists():
            return None, Problem("hold", "cv_missing", "Upload your CV on the page first. It is the only file Friday attaches.")
        root = cv.folder.resolve()
        try:
            data = mailio.read_attachment(root / cv.path.name, root)          # the mail module's checks: O_NOFOLLOW, one link, mine, %PDF-, 5 MB
            creds = self.d.mail.creds()
            if creds and mailio.holds_secret(Path(cv.path.name), data, creds[1]):
                return None, Problem("refuse", "cv_secret", "The CV file contains the Gmail app password. It was not sent.")
        except mailio.AttachmentRefused as e:
            return None, Problem("hold", "cv_refused", str(e))
        name = re.sub(r"[^\w .()-]", "", Path(meta.get("filename") or "CV.pdf").name)[:80] or "CV.pdf"
        return (data, name if name.lower().endswith(".pdf") else name + ".pdf"), None

    # ---- the rules ----
    def evaluate(self, r: dict, now: float, sending: bool = False, cv=None) -> Problem | None:
        """The first rule that stops this row, or None. `sending`: the CV must match the sha256 stored at Approve.
        `cv` is a result of `_cv()` that the caller already holds, so the bytes checked are the bytes sent."""
        d = self.d
        if d.is_paused():
            return Problem("hold", "paused", "Friday is paused. Nothing is sent until you switch her on. Switch her on, then press Approve.")
        if r["kind"] not in KINDS:
            return Problem("refuse", "kind", "Friday does not send this kind of email in v1. Freelance pitches are off.")
        to, subject = r["to_addr"], r["subject"]
        if not self._plain_address(to):
            return Problem("refuse", "recipient", "Friday writes to exactly one plain email address. CC and BCC are never used.")
        if not subject.strip() or len(subject) > 300 or re.search(r"[\r\n]", subject) or not r["body"].strip():
            return Problem("refuse", "subject", "The subject or the text is empty, or the subject is too long or has a line break.")
        from .route import not_for_hiring
        if not_for_hiring(to):
            return Problem("refuse", "not_hiring", "This address is for accessibility, privacy, legal, security, press or support. Friday does not write to it.")
        route = self._route_for(r)
        want = {"application": "email", "speculative": "speculative"}.get(r["kind"], r["kind"])
        if not route or route.get("kind") != want or norm_email(route.get("to", "")) != norm_email(to):
            return Problem("refuse", "route", "The address is not the one Friday found for this role. She sends only to the address on its route.")
        # blocklist
        bl = d.blocklist
        rules = bl.rules()
        for x in (to, route.get("company_domain") or "", r["domain"]):
            if x and blocklist_mod.blocked(x, rules.domains):
                return Problem("refuse", "blocklist", "This company or address is on your blocklist.")
        job = d.job_fetch.store.get(r["job_id"]) if r["job_id"] is not None else None       # the name, the board and the links count too
        if bl.company_blocked(r["company"], [job["job_url"], job["apply_url"]] if job else [], [to], rules=rules):
            return Problem("refuse", "blocklist", "This company or address is on your blocklist.")
        # suppression
        sup = {x for x in (r["domain"], registrable(to.rsplit("@", 1)[-1])) if x}
        if any(d.db.query("SELECT 1 FROM suppressed WHERE domain=?", (x,)) for x in sup):
            return Problem("refuse", "suppressed", "This domain asked Friday to stop. She does not write to it again.")
        try:
            pb = self.ledger.problem(r["domain"], now, ok_other_agent=True)        # here only "suppressed" can come back
        except LedgerError as e:
            return Problem("hold", "ledger", f"The shared ledger cannot be read ({e}) Friday waits.")
        if pb:
            return Problem("refuse", "suppressed", pb[1])
        # the same role, once
        if r["kind"] == "application":
            same = d.db.query("SELECT 1 FROM outbox WHERE id<>? AND state='sent' AND ((job_id IS NOT NULL AND job_id=?) OR "
                              "(company_norm<>'' AND company_norm=? AND title_norm<>'' AND title_norm=?))",
                              (r["id"], r["job_id"], r["company_norm"], r["title_norm"]))
            if same or d.scorer.applied_before({"job_id": r["job_id"], "company_norm": r["company_norm"], "title_norm": r["title_norm"]}):
                return Problem("refuse", "same_role", "You applied for this role before. Friday never writes twice for the same role.")
        # follow-ups: at most 2 for one first mail, none after a reply, none when the owner set the company aside
        if r["kind"] == "followup":
            src = json.loads(r["source"]) if isinstance(r.get("source"), str) else (r.get("source") or {})
            origin = int(src.get("origin_outbox") or 0)
            sent_before = [x for x in self.d.db.query("SELECT source FROM outbox WHERE id<>? AND state='sent' AND kind='followup'", (r["id"],))
                           if int((json.loads(x["source"] or "{}")).get("origin_outbox") or 0) == origin]
            if len(sent_before) >= 2:
                return Problem("refuse", "followup_max", "Friday sends at most 2 follow-ups for one application.")
            if self.d.db.query("SELECT 1 FROM company_replies WHERE key=?", (r["company_norm"],)):
                return Problem("refuse", "followup_reply", "This company already replied. Friday sends no follow-up.")
            if self.d.db.query("SELECT 1 FROM company_status WHERE key=? AND status IN ('rejected','not_interested','interview','offer','blocklisted')", (r["company_norm"],)):
                return Problem("refuse", "followup_status", "This company is closed, or you are already talking to it. Friday sends no follow-up.")
        # replies: none to a company that said no, one per thread in 7 days (DJ14KF M3)
        if r["kind"] == "reply":
            src = json.loads(r["source"]) if isinstance(r.get("source"), str) else (r.get("source") or {})
            origin = int(src.get("origin_outbox") or 0)
            if self.d.db.query("SELECT 1 FROM company_status WHERE key=? AND status IN ('rejected','not_interested','blocklisted')", (r["company_norm"],)) or \
                    self.d.db.query("SELECT 1 FROM company_replies WHERE key=? AND kind='rejection'", (r["company_norm"],)):
                return Problem("refuse", "reply_closed", "This company said no or is closed. Friday sends no reply.")
            for x in self.d.db.query("SELECT source, sent_ts FROM outbox WHERE id<>? AND kind='reply' AND state='sent' AND sent_ts>=?", (r["id"], now - 7 * 86400)):
                if int((json.loads(x["source"] or "{}")).get("origin_outbox") or 0) == origin:
                    return Problem("refuse", "reply_max", "Friday already sent a reply in this thread in the last 7 days.")
        # company limits
        shared = is_hiring_system(to.rsplit("@", 1)[-1].lower()) or not r["domain"]
        rows = d.db.query("SELECT job_id, title_norm, sent_ts FROM outbox WHERE id<>? AND state='sent' AND sent_ts>=? AND kind NOT IN ('followup','reply') AND "
                          "((company_norm<>'' AND company_norm=?) OR (?=0 AND domain=?))",
                          (r["id"], now - ROLES_DAYS * 86400, r["company_norm"], int(shared), r["domain"]))
        limits = r["kind"] in ("application", "speculative")
        if limits and any(x["sent_ts"] >= now - COMPANY_DAYS * 86400 for x in rows):
            return Problem("refuse", "company_60", f"Friday already wrote to this company in the last {COMPANY_DAYS} days. She writes at most once in {COMPANY_DAYS} days.")
        if limits and len({(x["job_id"], x["title_norm"]) for x in rows}) >= COMPANY_MAX_ROLES:
            return Problem("refuse", "company_90", f"Friday already wrote for {COMPANY_MAX_ROLES} roles at this company in the last {ROLES_DAYS} days.")
        # another agent used the domain
        try:
            pb = self.ledger.problem(r["domain"], now, bool(r["ok_other_agent"]))
        except LedgerError as e:
            return Problem("hold", "ledger", f"The shared ledger cannot be read ({e}) Friday waits.")
        if pb:
            return Problem("refuse", "suppressed", pb[1]) if pb[0] == "suppressed" else Problem("ok_needed", "other_agent", pb[1])
        # text
        creds = d.mail.creds()
        if not creds:
            return Problem("hold", "no_login", "Gmail is not connected yet.")
        text = f"{subject}\n{r['body']}"
        if config.holds_password(text, creds[1]):
            return Problem("refuse", "secret", "The text contains the Gmail app password. It was not sent.")
        ph = placeholders.find(text)
        if ph:
            return Problem("hold", "placeholder", f"The text still shows a placeholder ({ph[0][:40]}). Edit it, then press Approve.")
        others = {norm_email(a) for a in _ADDR_IN_TEXT.findall(text)} - {norm_email(to), norm_email(creds[0])}
        if others:
            return Problem("hold", "address_in_text", "The text names another email address. Edit it, then press Approve.")
        # the CV
        got, err = cv if cv is not None else self._cv()
        if err:
            return err
        if sending and (not r["cv_sha"] or _sha(got[0]) != r["cv_sha"]):
            return Problem("hold", "cv_changed", "Your CV changed after you approved this email. Check it, then press Approve again.")
        # caps and the window: these only make it wait
        cap_day, cap_hour = caps(self.d.db)
        if self.sent_today(now) >= cap_day:
            return Problem("wait", "cap_day", f"Daily cap reached: {cap_day} emails today. It goes out tomorrow by itself.")
        if self.sent_since(now - 3600) >= cap_hour:
            return Problem("wait", "cap_hour", f"Hourly cap reached: {cap_hour} emails in the last hour. It goes out by itself.")
        if not self.in_window(r, now):
            zone = self.recipient_zone(r).key
            return Problem("wait", "window", f"Waits for the sending window: weekdays {WINDOW_FROM:02d}:00 to {WINDOW_TO:02d}:00 in {zone}.")
        return None

    # ---- queue, edit, approve, undo, reject ----
    def queue(self, draft_id: int) -> dict:
        """A waiting email draft becomes an outbox row. The recipient and company come from the route and the job, not from the text."""
        with self.lock:
            draft = self.d.writer.get(draft_id)
            if not draft:
                return {"status": "rejected", "outbox_id": None, "reason": "There is no such draft."}
            if draft["kind"] not in KINDS:
                return {"status": "refused", "outbox_id": None, "reason": "Friday does not send this kind of email in v1. Freelance pitches are off."}
            held = self.d.db.query("SELECT id FROM outbox WHERE state IN ('waiting','held','undo','queued','sending','sent') AND "
                                   "(draft_id=? OR (? IS NOT NULL AND job_id=?)) ORDER BY id DESC LIMIT 1",
                                   (draft["id"], draft["job_id"], draft["job_id"]))
            if held:
                r = self._row(held[0]["id"])
                return self._result(r, reason="This role already has an email in the list.", already=True)
            if draft["channel"] != "email" or draft["state"] != "waiting":
                return {"status": "rejected", "outbox_id": None, "reason": "Only an email draft that waits for you can be sent."}
            if draft["kind"] in ("followup", "reply"):
                o = self._row(int((draft["source"] or {}).get("origin_outbox") or 0))
                if not o:
                    return {"status": "rejected", "outbox_id": None, "reason": "The first email is gone."}
                who = {"company": o["company"], "company_norm": o["company_norm"], "title_norm": o["title_norm"], "location": o["location"]}
            elif draft["kind"] == "application":
                job = next((j for j in self.d.job_fetch.store.all() if j["id"] == draft["job_id"]), None)
                if not job:
                    return {"status": "rejected", "outbox_id": None, "reason": "The job is gone."}
                who = {"company": job["company"], "company_norm": job["company_norm"], "title_norm": job["title_norm"], "location": job["location"]}
            else:
                co = next((c for c in self.d.dataset.companies() if c["id"] == draft["company_id"]), None)
                if not co:
                    return {"status": "rejected", "outbox_id": None, "reason": "The company is not in your list."}
                from .dedupe import norm_company
                who = {"company": co["name"], "company_norm": norm_company(co["name"]), "title_norm": "", "location": ""}
            now = self.now()
            r0 = {"kind": draft["kind"], "job_id": draft["job_id"], "company_id": draft["company_id"], **who, "to_addr": draft["to_addr"],
                  "source": draft["source"]}
            route = self._route_for(r0) or {}
            to_domain = draft["to_addr"].rsplit("@", 1)[-1]
            # A shared hiring-system domain is never reserved: it stands for many companies (DJ10 M4). No domain, no reservation.
            domain = ledger_mod.norm(route.get("company_domain") or ("" if is_hiring_system(to_domain.lower()) else to_domain))
            with self.d.db.tx() as c:
                oid = c.execute(
                    "INSERT INTO outbox(draft_id, kind, job_id, company_id, company, company_norm, title_norm, location, domain, to_addr, subject, "
                    "body, source, state, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'waiting', ?)",
                    (draft["id"], draft["kind"], draft["job_id"], draft["company_id"], who["company"], who["company_norm"], who["title_norm"],
                     who["location"], domain, draft["to_addr"], draft["subject"], draft["body"], json.dumps(draft["source"]), now)).lastrowid
            r = self._row(oid)
            p = self.evaluate(r, now)
            if p and p.kind == "refuse":
                self._set(oid, state="refused", reason=p.text, decided=now)
            elif p and p.kind == "hold":
                self._set(oid, reason=p.text)
            self._event("queue", self._row(oid), "Waiting for you." if not (p and p.kind == "refuse") else "Refused: " + p.text)
            return self._result(self._row(oid))

    def edit(self, oid: int, subject: str | None = None, body: str | None = None) -> dict:
        """The owner's change to the text of a waiting or held email. It clears any approval."""
        with self.lock:
            r = self._row(oid)
            if not r or r["state"] not in ("waiting", "held"):
                return {"status": "rejected", "outbox_id": oid, "reason": "Only an email that waits for you can be edited. Press Undo first."}
            subject = (r["subject"] if subject is None else subject).strip()
            body = r["body"] if body is None else body
            if not subject or re.search(r"[\r\n]", subject) or len(subject) > 300 or not body.strip() or len(body) > 20000:
                return {"status": r["state"], "outbox_id": oid, "reason": "The subject must be one line, and the text must not be empty."}
            ph = placeholders.find(f"{subject}\n{body}")
            if ph:
                return {"status": r["state"], "outbox_id": oid, "reason": f"The text still shows a placeholder ({ph[0][:40]})."}
            self._set(oid, subject=subject, body=body, state="waiting", reason="", cv_sha="", approved_ts=None, undo_until=None)
            return self._result(self._row(oid))

    def approve(self, oid: int, ok_other_agent: bool = False) -> dict:
        """Start the 10-second Undo. Rules that can already be checked are checked now, so the owner learns early."""
        with self.lock:
            r = self._row(oid)
            if not r or r["state"] not in (*APPROVABLE, "undo", "queued"):
                return {"status": "rejected", "outbox_id": oid, "reason": "This email is not waiting for approval."}
            if r["state"] in ("undo", "queued"):
                return self._result(r, already=True, undo_until=r["undo_until"])
            now = self.now()
            r = {**r, "ok_other_agent": int(bool(ok_other_agent))}
            cvr = self._cv()
            got = cvr[0]
            p = self.evaluate(r, now, cv=cvr)
            if p and p.kind == "ok_needed":
                return self._result(self._row(oid), "needs_ok", p.text)
            if p and p.kind == "refuse":
                self._set(oid, state="refused", reason=p.text, decided=now)
                self._event("send_refused", r, p.text)
                return self._result(self._row(oid))
            if p and p.kind in ("hold",):
                self._set(oid, state="held", reason=p.text)
                return self._result(self._row(oid))
            self._set(oid, state="undo", reason="", cv_sha=_sha(got[0]), ok_other_agent=int(bool(ok_other_agent)), approved_ts=now,
                      undo_until=now + UNDO_SECONDS, decided=now)
            self._event("approved", r, f"Approved. You can undo for {UNDO_SECONDS} seconds.")
            return self._result(self._row(oid), undo_until=now + UNDO_SECONDS)

    def undo(self, oid: int) -> dict:
        """Within the 10 seconds after Approve, back to waiting. After that, too late: use Reject while it still waits for a cap or the window."""
        pressed = self.now()                  # read when the request arrives, before any wait for the lock (DJ11 K1)
        with self.lock:
            r = self._row(oid)
            if not r or r["state"] != "undo" or pressed >= (r["undo_until"] or 0):
                return {"status": r["state"] if r else "rejected", "outbox_id": oid, "reason": "Too late to undo. The 10 seconds are over."}
            self._set(oid, state="waiting", reason="You pressed Undo. Nothing was sent.", cv_sha="", ok_other_agent=0, approved_ts=None, undo_until=None)
            self._event("undo", r, "Undo. Nothing was sent.")
            return self._result(self._row(oid))

    def reject(self, oid: int) -> dict:
        with self.lock:
            r = self._row(oid)
            if not r or r["state"] not in REJECTABLE:
                return {"status": r["state"] if r else "rejected", "outbox_id": oid, "reason": "This email cannot be rejected now."}
            self._set(oid, state="rejected", reason="Rejected by you.", decided=self.now(), approved_ts=None)
            with self.d.db.tx() as c:
                c.execute("UPDATE drafts SET state='rejected' WHERE id=? AND state='waiting'", (r["draft_id"],))
            self._event("rejected", r, "Rejected.")
            return self._result(self._row(oid))

    def suppress(self, domain: str, reason: str = "asked to stop") -> int:
        """A company asked Friday to stop. Kept for good, in her list and in the shared ledger. Open mail for it is refused.
        Phase 2: reply reading will call this. Nothing calls it yet (kept on purpose, with its tests)."""
        d = ledger_mod.norm(domain)
        if not d:
            return 0
        with self.lock:
            with self.d.db.tx() as c:
                c.execute("INSERT OR IGNORE INTO suppressed(domain, reason, ts) VALUES(?,?,?)", (d, reason[:100], self.now()))
            self.ledger.mark_suppressed(d)
            n = 0
            for r in self.list(*OPEN):
                if r["state"] != "sending" and (r["domain"] == d or registrable(r["to_addr"].rsplit("@", 1)[-1]) == d):
                    self._set(r["id"], state="refused", reason="This domain asked Friday to stop.", decided=self.now())
                    n += 1
            return n

    # ---- the one send ----
    def recover_sending(self) -> int:
        """At start-up: a row still 'sending' means the process stopped mid-send. Never send it again by itself."""
        with self.d.db.tx() as c:
            return c.execute("UPDATE outbox SET state='held', reason=?, approved_ts=NULL, cv_sha='' WHERE state='sending'",
                             ("Friday stopped while sending. Check Gmail Sent before you press Approve.",)).rowcount

    def send(self, oid: int) -> dict:
        """Send one approved email whose Undo is over. Returns {status: sent|queued|held|refused|failed|rejected, outbox_id, reason}.
        A row that nobody approved, or whose Undo is not over, is never sent. The lock is held for the claim, the checks and the record,
        never while Gmail answers, so an Undo pressed in time is never made to wait for another email's SMTP (DJ11 K1)."""
        with self.lock:
            now = self.now()
            with self.d.db.tx() as c:
                got = c.execute("UPDATE outbox SET state='sending' WHERE id=? AND state IN ('undo','queued') AND approved_ts IS NOT NULL "
                                "AND undo_until IS NOT NULL AND undo_until<=?", (int(oid), now)).rowcount
            if not got:
                r = self._row(oid)
                why = "The 10 seconds of Undo are not over yet." if r and r["state"] == "undo" else "This email is not approved."
                return {"status": "rejected", "outbox_id": oid, "reason": why}
        try:
            with self.lock:
                ready = self._prepare_claimed(oid, now)
            if isinstance(ready, dict):
                return ready
            return self._deliver_claimed(*ready, now)
        finally:
            with self.lock:
                r = self._row(oid)
                if r and r["state"] == "sending":       # an unexpected error must not leave the row claimed for ever
                    self._set(oid, state="held", approved_ts=None, cv_sha="", reason="Sending stopped with an error. Check Gmail Sent, then press Approve.")

    def _prepare_claimed(self, oid: int, now: float):
        """Every check, the message, the ledger reservation. Returns a result (nothing to deliver) or (row, message, login, ledger before)."""
        r = self._row(oid)
        cvr = self._cv()
        got = cvr[0]
        p = self.evaluate(r, now, sending=True, cv=cvr)
        if p:
            if p.kind == "wait":                            # a cap or the window: the 6-hour count of connection trouble starts again
                self._set(oid, state="queued", reason=p.text, first_fail=None, retry_at=None)
            elif p.kind == "refuse":
                self._set(oid, state="refused", reason=p.text, decided=now)
                self._event("send_refused", r, p.text)
            else:                                           # hold, ok_needed: the approval is cleared; the owner decides again
                self._set(oid, state="held", reason=p.text, approved_ts=None, cv_sha="")
                self._event("send_held", r, p.text)
            return self._result(self._row(oid))
        creds = self.d.mail.creds()
        data, name = got
        try:
            msg = mailio.build_message(creds[0], r["to_addr"], r["subject"], r["body"], [(Path(name), data, "application/pdf")])
        except Exception as e:  # noqa: BLE001
            self._set(oid, state="failed", reason=config.scrub(f"Friday could not build the email ({type(e).__name__}).", self.d.cfg.secrets_file)[:300])
            return self._result(self._row(oid))
        tos = [a for _, a in getaddresses([str(x) for x in msg.get_all("To", [])])]
        if len(tos) != 1 or norm_email(tos[0]) != norm_email(r["to_addr"]) or msg.get("Cc") or msg.get("Bcc") \
                or len(list(msg.iter_attachments())) != 1:
            self._set(oid, state="refused", reason="The email did not have one recipient and one attachment. It was not sent.", decided=now)
            return self._result(self._row(oid))
        # Reserve the domain BEFORE any delivery, so another tool and the mail module see it even if the send stops half way.
        try:
            before = self.ledger.reserve(r["domain"], now, bool(r["ok_other_agent"])) if r["domain"] else None
        except LedgerRefused as e:
            self._set(oid, state="refused" if e.kind == "suppressed" else "held", reason=str(e), approved_ts=None, cv_sha="")
            return self._result(self._row(oid))
        except LedgerError as e:
            self._set(oid, state="held", reason=f"The shared ledger cannot be read ({e}) Friday waits.", approved_ts=None, cv_sha="")
            return self._result(self._row(oid))
        return r, msg, creds, before

    def _deliver_claimed(self, r: dict, msg, creds, before, now: float) -> dict:
        """The hand-over to Gmail (no lock held), then the record. The row is 'sending', so nothing else can send it."""
        oid = r["id"]
        try:
            mailio.deliver(creds, msg, self.d.mail.f)
        except mailio.ConnectFailed:
            # Gmail was not reached, so nothing left: give the reservation back and try again at the next check.
            with self.lock:
                if r["domain"]:
                    self.ledger.restore(r["domain"], before)
                first = r["first_fail"] or now
                if now - first > RETRY_WINDOW:
                    self._set(oid, state="failed", reason="Gmail could not be reached for 6 hours, so this email was not sent.", first_fail=first)
                else:
                    self._set(oid, state="queued", reason="Gmail could not be reached. Friday tries again in a few minutes.", first_fail=first,
                              retry_at=now + RETRY_AFTER)
                return self._result(self._row(oid))
        except Exception as e:  # noqa: BLE001: may have happened after the hand-over, so it is never retried
            with self.lock:
                self._set(oid, state="failed", reason=config.scrub(f"Sending failed ({type(e).__name__}): {e}", self.d.cfg.secrets_file)[:300], decided=now)
                self._event("send_failed", r, "Sending failed. Check Gmail Sent before you try again.")
                return self._result(self._row(oid))
        with self.lock:
            mid = str(msg["Message-ID"])
            self._set(oid, state="sent", reason="", sent_ts=now, message_id=mid, retry_at=None)
            self.d.db.add_sent_id(mid, self.d.clock)
            with self.d.db.tx() as c:
                c.execute("UPDATE drafts SET state='sent' WHERE id=?", (r["draft_id"],))
            self.d.scorer.mark_applied({"job_id": r["job_id"], "company_norm": r["company_norm"], "title_norm": r["title_norm"]}, "email")
            self._event("sent", r, "Sent.")
            return self._result(self._row(oid))

    def release_due(self) -> int:
        """The tick: send every approved email whose Undo is over (and whose retry time has come). Returns how many were sent.
        No lock is held across rows or across SMTP: `send` claims each row for itself."""
        n = 0
        now = self.now()
        due = self.d.db.query("SELECT id FROM outbox WHERE state IN ('undo','queued') AND approved_ts IS NOT NULL AND undo_until<=? "
                              "AND (retry_at IS NULL OR retry_at<=?) ORDER BY approved_ts, id", (now, now))
        for row in due:
            try:
                n += self.send(row["id"])["status"] == "sent"
            except Exception as e:  # noqa: BLE001: one bad row must not block the rest
                self.d.db.event("error", config.scrub(f"Email {row['id']} could not be sent ({type(e).__name__}).", self.d.cfg.secrets_file), self.d.clock)
        return n

    # ---- the page ----
    def counts(self) -> dict[str, int]:
        return {r["state"]: r["n"] for r in self.d.db.query("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state")}

    def company_names(self, states, limit: int = 40, newest_sent_first: bool = False) -> list[str]:
        """Company names of the rows in these states, oldest first (or newest sent first). Names come from job posts: untrusted text."""
        marks = ",".join("?" * len(states))
        order = "sent_ts DESC" if newest_sent_first else "id"
        return [r["company"] for r in self.d.db.query(f"SELECT company FROM outbox WHERE state IN ({marks}) ORDER BY {order} LIMIT ?", (*states, limit))]

    def status(self) -> dict:
        now = self.now()
        counts = self.counts()
        cap_day, cap_hour = caps(self.d.db)
        return {"counts": counts, "sent_today": self.sent_today(now), "sent_last_hour": self.sent_since(now - 3600), "cap_day": cap_day,
                "cap_hour": cap_hour, "undo_seconds": UNDO_SECONDS}
