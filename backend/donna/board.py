"""Friday's company board (DJ14I): one row per company with a status that moves by itself and that the owner can set by hand.

Statuses, from the quietest to the furthest. The furthest one wins:
  not_checked, checking, no_roles, site_blocked, roles_found, draft_ready, applied, reply, interview, offer, rejected,
  then the two that end it: not_interested (the owner) and blocklisted (the owner's blocklist, which always wins).

Friday moves a status from her own records: roles fetched, a score of 70 or more, a draft or form card waiting, a mail sent or a role
marked applied, a reply in her Gmail (`DonnaMail.scan_replies`), rejection wording in a reply. Rejection wording is a code rule, so it
shows as "Rejected?" until the owner confirms it. Every change is logged with its time and reason.

The owner can set any status and add a note. A hand status is never overwritten by an automatic one that is lower. Moving a company
to Applied by hand sends nothing. Reply text is untrusted: only the code rules below read it; a short cleaned snippet is kept for the page.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from .dedupe import norm_company
from .domains import registrable

SCHEMA = """
CREATE TABLE IF NOT EXISTS company_status (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, status TEXT NOT NULL,
    auto TEXT NOT NULL, hand TEXT NOT NULL DEFAULT '', unconfirmed INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '', changed_ts REAL NOT NULL, added_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS company_log (id INTEGER PRIMARY KEY, key TEXT NOT NULL, ts REAL NOT NULL, status TEXT NOT NULL,
    reason TEXT NOT NULL, by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS company_log_key ON company_log(key);
CREATE TABLE IF NOT EXISTS company_replies (message_id TEXT PRIMARY KEY, key TEXT NOT NULL, outbox_id INTEGER, ts REAL NOT NULL,
    subject TEXT NOT NULL, from_name TEXT NOT NULL, snippet TEXT NOT NULL, kind TEXT NOT NULL);
"""

# id, label, rank. A higher rank is further along.
STATUS_LIST = (
    ("not_checked", "Not checked", 0), ("checking", "Checking", 1), ("no_roles", "No matching roles", 2),
    ("site_blocked", "Site blocks visits (skipped)", 2), ("roles_found", "Roles found", 3), ("draft_ready", "Draft ready", 4),
    ("applied", "Applied", 5), ("reply", "Reply received", 6), ("interview", "Interview", 7), ("offer", "Offer", 8),
    ("rejected", "Rejected", 9), ("not_interested", "Not interested", 100), ("blocklisted", "Blocklisted", 101),
)
LABEL = {i: l for i, l, _r in STATUS_LIST}
RANK = {i: r for i, _l, r in STATUS_LIST}
HAND_OK = tuple(i for i, _l, _r in STATUS_LIST if i not in ("checking", "blocklisted"))
# Columns of the board view.
STAGES = (("watching", "Watching", ("not_checked", "checking", "no_roles", "site_blocked")), ("found", "Found a fit", ("roles_found",)),
          ("draft", "Waiting for you", ("draft_ready",)), ("sent", "Sent", ("applied",)),
          ("replied", "Replied", ("reply", "interview", "offer")), ("closed", "Closed", ("rejected", "not_interested", "blocklisted")))
# amber = needs you, green = Donna is working, grey = waiting for time, red = stopped.
TONE = {"draft_ready": "amber", "reply": "amber", "interview": "amber", "offer": "amber", "roles_found": "green", "checking": "green",
        "not_checked": "grey", "no_roles": "grey", "applied": "grey", "site_blocked": "red", "blocklisted": "red", "rejected": "grey",
        "not_interested": "grey"}
NEXT = {
    "not_checked": ("Waiting for the next check", "Friday reads each job board once a day."),
    "checking": ("Friday is reading the job board", "This takes a minute."),
    "no_roles": ("No role fits right now", "Friday looks again tomorrow."),
    "site_blocked": ("Search this company by hand", "The site blocks automated visits, so Friday skipped it."),
    "roles_found": ("Friday is scoring the roles", "A score of 70 or more gets a draft."),
    "draft_ready": ("Read the draft and press Approve", "A draft is waiting in Waiting for you."),
    "applied": ("Waiting for a reply", "Friday reads her Gmail for replies."),
    "reply": ("Read the reply", "The company wrote back."),
    "interview": ("Prepare for the interview", "You set this status."),
    "offer": ("Decide on the offer", "You set this status."),
    "rejected": ("Closed", "The company said no."),
    "not_interested": ("Closed", "You are not interested."),
    "blocklisted": ("Stopped: this company is on your blocklist", "Friday never writes to it."),
}
REJECTION = re.compile(r"unfortunately|regret to (?:inform|tell)|we(?:'ve| have) decided to (?:move|proceed|go) (?:forward )?with (?:other|another)|"
                       r"not (?:be )?(?:moving|proceeding) forward|will not be (?:moving|proceeding)|position (?:has been|is) filled|"
                       r"not (?:been )?selected|unable to (?:offer|move)|decided not to (?:move|proceed)|other candidates", re.I)
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:https?:|www\.)\S+", re.I)
BLOCK_TEXT = "Site blocks automated visits, skipped"
SNIPPET = 300
REPLY_DAYS = 45


def clean_snippet(text: str) -> str:
    return " ".join(_ADDRESS.sub(" ", text or "").split())[:SNIPPET]


def rejection_wording(text: str) -> bool:
    return bool(REJECTION.search(text or ""))


def key_of(name: str) -> str:
    return norm_company(name) or (name or "").strip().lower()


class Board:
    def __init__(self, donna):
        self.d = donna

    # ------------------------------------------------------------------ what Donna knows
    def _universe(self) -> dict[str, dict]:
        """key -> {name, country, sources, domain, board_ids, insights}. Every company from sheets, lists, boards and jobs."""
        db, out = self.d.db, {}
        sheet_names = {f"sheet:{r['id']}": r["name"] for r in db.query("SELECT id, name FROM sheets")}

        def entry(name: str) -> dict | None:
            k = key_of(name)
            if not k:
                return None
            return out.setdefault(k, {"key": k, "name": name.strip(), "country": "", "sources": [], "domain": "", "board_ids": [],
                                      "careers_url": "", "insights": {}, "notes": ""})

        def source(e, label):
            if label and label not in e["sources"]:
                e["sources"].append(label)
        for c in db.query("SELECT * FROM companies"):
            e = entry(c["name"])
            if not e:
                continue
            e.update(country=e["country"] or c["country"], domain=e["domain"] or c["domain"], careers_url=e["careers_url"] or c["careers_url"],
                     notes=e["notes"] or c["notes"], insights={**json.loads(c["insights"] or "{}"), **e["insights"]})
            src = c["source"]
            source(e, sheet_names.get(src, "A removed sheet") if src.startswith("sheet:") else
                   {"upload": "Your file", "google sheet": "Your sheet", "dataset": "Your list"}.get(src, src[:40] or "Your list"))
            if c["board_id"]:
                e["board_ids"].append(c["board_id"])
        for b in db.query("SELECT * FROM boards"):
            e = entry(b["company"])
            if e:
                if b["id"] not in e["board_ids"]:
                    e["board_ids"].append(b["id"])
                source(e, "Starter list" if b["source"] == "seed" else "Your list" if not e["sources"] else "")
        for j in db.query("SELECT company FROM jobs GROUP BY company_norm"):
            e = entry(j["company"])
            if e and not e["sources"]:
                source(e, "Job boards")
        for r in db.query("SELECT company FROM outbox WHERE company != '' GROUP BY company_norm"):
            e = entry(r["company"])
            if e and not e["sources"]:
                source(e, "Job boards")
        return out

    def _evidence(self) -> dict[str, dict]:
        """key -> facts the status comes from. A few set-wide queries, not one per company."""
        db = self.d.db
        ev: dict[str, dict] = {}

        def get(name: str) -> dict:
            return ev.setdefault(key_of(name), {"jobs": 0, "best": None, "drafts": 0, "form": 0, "sent": 0, "applied": [], "replies": 0,
                                                "suspect": 0, "read_ok": [], "blocked": False, "failed": 0})
        scores = {r["job_id"]: r for r in db.query("SELECT job_id, state, total FROM job_scores")}
        for j in db.query("SELECT id, company FROM jobs WHERE state='live'"):
            e = get(j["company"])
            e["jobs"] += 1
            s = scores.get(j["id"])
            if s and s["total"] is not None and (e["best"] is None or s["total"] > e["best"]):
                e["best"] = s["total"]
        jobco = {j["id"]: j["company"] for j in db.query("SELECT id, company FROM jobs")}
        for r in db.query("SELECT job_id, company, state FROM drafts WHERE state='waiting'"):
            name = r["company"] or jobco.get(r["job_id"], "")
            if name:
                get(name)["drafts"] += 1
        for r in db.query("SELECT company, state FROM outbox WHERE company != ''"):
            e = get(r["company"])
            if r["state"] in ("waiting", "held", "undo", "queued", "sending"):
                e["drafts"] += 1
            elif r["state"] == "sent":
                e["sent"] += 1
        for r in db.query("SELECT company, job_id FROM drafts WHERE state='failed'"):
            name = r["company"] or jobco.get(r["job_id"], "")
            if name:
                get(name)["failed"] = get(name).get("failed", 0) + 1
        forms = {c["job_id"] for c in self.d.router.form_cards()}
        for jid in forms:
            if jid in jobco:
                get(jobco[jid])["form"] += 1
        for a in db.query("SELECT company_norm, how FROM applied WHERE company_norm != ''"):
            ev.setdefault(a["company_norm"], get(a["company_norm"]))["applied"].append(a["how"])
        for r in db.query("SELECT key, kind, COUNT(*) AS n FROM company_replies GROUP BY key, kind"):
            e = ev.setdefault(r["key"], get(r["key"]))
            if r["kind"] != "auto":                      # an automatic acknowledgement is not a reply from a person
                e["replies"] += r["n"]
            e["suspect"] += r["n"] if r["kind"] == "rejection" else 0
            e["invite"] = e.get("invite", 0) + (r["n"] if r["kind"] == "invite" else 0)
        scopes = {r["scope"]: r for r in db.query("SELECT * FROM fetch_scopes WHERE scope LIKE 'board:%'")}
        today = self.d.today()
        self._scopes = (scopes, today)
        return ev

    def _board_state(self, u: dict, boards: dict, scopes: dict, today: str) -> tuple[str, str]:
        """(no_roles | site_blocked | checking | '', reason) from the company's job boards."""
        status = ""
        for bid in u["board_ids"]:
            b = boards.get(bid)
            if not b or b["state"] not in ("active", "moved"):
                continue
            sc = scopes.get(f"board:{b['ats']}:{b['token']}")
            if sc and sc["error"] == BLOCK_TEXT:
                return "site_blocked", BLOCK_TEXT
            if sc and sc["last_ok_day"]:
                status = status or "no_roles"
            elif not sc or sc["last_day"] != today:
                if self.d.job_fetch.running:
                    return "checking", "Friday is reading the job board."
        return status, "The job board has no open roles that Friday kept." if status else ""

    def _auto(self, u: dict, e: dict, boards: dict, scopes: dict, today: str, blocked: set, rules) -> tuple[str, str, bool]:
        """(status, reason, unconfirmed) from Friday's own records. The furthest evidence wins."""
        if self.d.blocklist.company_blocked(u["name"], [x for x in (u["domain"], u["careers_url"]) if x], rules=rules):
            return "blocklisted", "The company is on your blocklist.", False
        if e["suspect"]:
            return "rejected", "A reply has rejection wording. Confirm it or dismiss it.", True
        if e.get("invite"):
            return "interview", "A reply invites you to talk.", False
        if e["replies"]:
            return "reply", "A reply came in Friday's Gmail.", False
        if e["sent"]:
            return "applied", "Friday sent a mail to this company.", False
        if e["applied"]:
            how = e["applied"][0]
            return "applied", {"sheet": "Your sheet marks it as applied.", "form": "You marked the form card as applied."}.get(how, "Marked as applied."), False
        if e["drafts"]:
            return "draft_ready", "A draft is waiting for you.", False
        if e["jobs"]:
            return "roles_found", f"{e['jobs']} open role{'s' if e['jobs'] != 1 else ''} found.", False
        if registrable(u["domain"]) in blocked:
            return "site_blocked", BLOCK_TEXT, False
        st, why = self._board_state(u, boards, scopes, today)
        return (st, why, False) if st else ("not_checked", "Friday has not read this company yet.", False)

    # ------------------------------------------------------------------ keeping the table current
    def refresh(self) -> dict:
        """Work out every company's status again. Writes a log line for each change. A hand status stays unless a higher automatic one comes."""
        db, now = self.d.db, self.d.clock()
        uni, ev = self._universe(), self._evidence()
        for k in ev:                                  # a company known only from a mail or an applied row still gets a row
            if k not in uni and k:
                nm = db.query("SELECT company FROM outbox WHERE company_norm=? LIMIT 1", (k,))
                uni[k] = {"key": k, "name": nm[0]["company"] if nm else k.title(), "country": "", "sources": ["Job boards"], "domain": "",
                          "board_ids": [], "careers_url": "", "insights": {}, "notes": ""}
        scopes, today = self._scopes
        boards = {b["id"]: b for b in db.query("SELECT * FROM boards")}
        blocked = {r["domain"] for r in db.query("SELECT domain FROM blocked_sites WHERE day=?", (today,))}
        rules = self.d.blocklist.rules()
        have = {r["key"]: r for r in db.query("SELECT * FROM company_status")}
        blank = {"jobs": 0, "best": None, "drafts": 0, "form": 0, "sent": 0, "applied": [], "replies": 0, "suspect": 0, "read_ok": [], "blocked": False}
        changes = 0
        with db.tx() as c:
            for k, u in uni.items():
                auto, why, unconf = self._auto(u, ev.get(k, blank), boards, scopes, today, blocked, rules)
                old = have.get(k)
                hand = old["hand"] if old else ""
                status, reason, by = auto, why, "donna"
                if auto != "blocklisted" and hand and not (RANK[auto] > RANK[hand] and hand != "not_interested"):
                    status, reason, by, unconf = hand, "You set this status.", "owner", False
                if not old:
                    c.execute("INSERT INTO company_status(key, name, status, auto, hand, unconfirmed, reason, changed_ts, added_ts) "
                              "VALUES(?, ?, ?, ?, '', ?, ?, ?, ?)", (k, u["name"][:120], status, auto, int(unconf), reason, now, now))
                    c.execute("INSERT INTO company_log(key, ts, status, reason, by) VALUES(?, ?, ?, ?, 'donna')",
                              (k, now, status, "Added to the board. " + reason))
                    changes += 1
                    continue
                if old["status"] != status or bool(old["unconfirmed"]) != unconf:
                    c.execute("UPDATE company_status SET status=?, auto=?, unconfirmed=?, reason=?, name=?, changed_ts=? WHERE key=?",
                              (status, auto, int(unconf), reason, u["name"][:120], now, k))
                    c.execute("INSERT INTO company_log(key, ts, status, reason, by) VALUES(?, ?, ?, ?, ?)", (k, now, status, reason, by))
                    changes += 1
                elif old["auto"] != auto or old["name"] != u["name"][:120]:      # no write for a row that did not change
                    c.execute("UPDATE company_status SET auto=?, name=? WHERE key=?", (auto, u["name"][:120], k))
        self._uni, self._ev = uni, ev
        return {"companies": len(uni), "changes": changes}

    # ------------------------------------------------------------------ replies
    def read_replies(self) -> dict:
        """One read-only pass over Gmail for replies to mail Friday sent. A rejection wording makes the company 'Rejected?'."""
        from .mail import NoLogin, Paused
        db = self.d.db
        sent = {r["message_id"]: r for r in db.query("SELECT id, company_norm, company, message_id FROM outbox WHERE state='sent' AND message_id != ''")}
        from .db import norm_message_id
        known = {norm_message_id(m): r for m, r in sent.items() if norm_message_id(m)}
        if not known:
            return {"ok": True, "replies": 0, "new": 0}
        if self.d.is_paused():
            return {"ok": False, "error": "Friday is paused."}
        since = date.fromtimestamp(self.d.clock()) - timedelta(days=REPLY_DAYS)
        try:
            found = self.d.mail.scan_replies(since, set(known))
        except (NoLogin, Paused):
            return {"ok": False, "error": "Friday's Gmail login is not saved yet."}
        except Exception as e:  # noqa: BLE001: one failed read must not stop the loop
            return {"ok": False, "error": f"Gmail could not be read ({type(e).__name__})."}
        new = 0
        rejected_keys: set[str] = set()
        with db.tx() as c:
            for f in found:
                row = known.get(f["in_reply_to"])
                mid = norm_message_id(f["message_id"])
                if not row or not mid or c.execute("SELECT 1 FROM company_replies WHERE message_id=?", (mid,)).fetchone():
                    continue
                from .intel import classify_reply
                kind = classify_reply(f["text"], f["subject"])
                c.execute("INSERT INTO company_replies(message_id, key, outbox_id, ts, subject, from_name, snippet, kind, from_addr) VALUES(?,?,?,?,?,?,?,?,?)",
                          (mid, row["company_norm"] or key_of(row["company"]), row["id"], f["ts"] or self.d.clock(), f["subject"],
                           f["from_name"], clean_snippet(f["text"]), kind, str(f.get("from") or "")[:200]))
                new += 1
                if kind == "rejection":
                    rejected_keys.add(row["company_norm"] or key_of(row["company"]))
        for k in rejected_keys:                        # a rejection withdraws waiting reply drafts for that company (DJ14KF M3)
            self.d.intel.withdraw_replies(k)
        db.event("board", f"Replies: {len(found)} found, {new} new", self.d.clock)
        self.refresh()
        return {"ok": True, "replies": len(found), "new": new}

    # ------------------------------------------------------------------ the owner
    def _row(self, cid: int) -> dict | None:
        r = self.d.db.query("SELECT * FROM company_status WHERE id=?", (cid,))
        return r[0] if r else None

    def set_status(self, cid: int, status: str, note: str | None = None) -> dict:
        """The owner sets a status by hand ('auto' gives it back to Friday). It sends nothing, also for Applied."""
        r = self._row(cid)
        if not r:
            return {"ok": False, "error": "No such company."}
        if status != "auto" and status not in HAND_OK:
            return {"ok": False, "error": "Choose one of the statuses in the list."}
        if r["status"] == "blocklisted" and status != "auto":
            return {"ok": False, "error": "This company is on your blocklist. Remove it there first."}
        with self.d.db.tx() as c:
            c.execute("UPDATE company_status SET hand=?, note=COALESCE(?, note), unconfirmed=CASE WHEN ?='rejected' THEN 0 ELSE unconfirmed END "
                      "WHERE id=?", ("" if status == "auto" else status, None if note is None else note[:1000], status, cid))
            if status == "rejected":                    # the owner confirmed the rejection
                c.execute("UPDATE company_replies SET kind='rejection' WHERE key=? AND kind='rejection'", (r["key"],))
            if status != "auto":
                c.execute("INSERT INTO company_log(key, ts, status, reason, by) VALUES(?, ?, ?, 'You set this status.', 'owner')",
                          (r["key"], self.d.clock(), status))
        self.refresh()
        return {"ok": True, "company": self.company(cid)}

    def set_note(self, cid: int, note: str) -> dict:
        if not self._row(cid):
            return {"ok": False, "error": "No such company."}
        with self.d.db.tx() as c:
            c.execute("UPDATE company_status SET note=? WHERE id=?", (note.strip()[:1000], cid))
        return {"ok": True, "company": self.company(cid)}

    def dismiss_rejection(self, cid: int) -> dict:
        """The owner says the 'Rejected?' guess was wrong: those replies count as plain replies."""
        r = self._row(cid)
        if not r:
            return {"ok": False, "error": "No such company."}
        with self.d.db.tx() as c:
            c.execute("UPDATE company_replies SET kind='reply' WHERE key=? AND kind='rejection'", (r["key"],))
            c.execute("INSERT INTO company_log(key, ts, status, reason, by) VALUES(?, ?, ?, 'You dismissed the rejection guess.', 'owner')",
                      (r["key"], self.d.clock(), r["status"]))
        self.refresh()
        return {"ok": True, "company": self.company(cid)}

    # ------------------------------------------------------------------ for the page
    def _view(self, r: dict, u: dict | None, e: dict | None) -> dict:
        u = u or {"country": "", "sources": [], "domain": ""}
        e = e or {}
        status = r["status"]
        title, why = NEXT[status]
        label, tone = ("Rejected?" if r["unconfirmed"] else LABEL[status]), ("amber" if r["unconfirmed"] else TONE[status])
        if r["unconfirmed"]:
            title, why = "Confirm or dismiss the rejection", "A reply has rejection wording. Friday is not sure."
        elif status in ("roles_found", "not_checked", "no_roles") and e.get("failed"):
            label, tone, title, why = "Stopped by the check", "amber", "Check the words in the draft", "The draft names facts Friday cannot back up. Tick the true ones."
        elif status in ("roles_found", "not_checked", "no_roles") and e.get("form"):
            label, tone, title, why = "Needs a form", "amber", "Apply on their site", "No hiring address is published. Friday prepared the answers."
        needs = bool(r["unconfirmed"] or status in ("draft_ready", "reply", "interview", "offer") or e.get("failed") or e.get("form")) and status not in ("rejected", "not_interested", "blocklisted", "applied")
        return {"id": r["id"], "name": r["name"], "country": u.get("country", ""), "source": ", ".join(u.get("sources", [])[:3]),
                "status": status, "label": label, "tone": tone, "needs": needs,
                "unconfirmed": bool(r["unconfirmed"]), "hand": r["hand"], "roles": e.get("jobs", 0), "best": e.get("best"), "next": title, "why": why,
                "reason": r["reason"], "changed_ts": r["changed_ts"], "note": r["note"], "failed": e.get("failed", 0), "warm": getattr(self, "_warm", {}).get(r["key"], 0),
                "grade": ("" if e.get("best") is None else "A" if e["best"] >= 80 else "B" if e["best"] >= 70 else "C" if e["best"] >= 60 else ""), "fit": e.get("best") is not None, "s70": (e.get("best") or 0) >= 70, "step": self._step(status, e)}

    @staticmethod
    def _step(status: str, e: dict) -> list[str]:
        """The funnel steps this company is in (a company can be in several): found, fit, s70, drafted, stopped, ready, sent."""
        out = []
        if e.get("jobs"):
            out.append("found")
        if e.get("best") is not None:
            out.append("fit")
        if (e.get("best") or 0) >= 70:
            out.append("s70")
        if e.get("drafts") or e.get("sent") or status in ("draft_ready", "applied", "reply", "interview", "offer"):
            out.append("drafted")
        if e.get("failed"):
            out.append("stopped")
        if status == "draft_ready":
            out.append("ready")
        if status in ("applied", "reply", "interview", "offer") or e.get("sent"):
            out.append("sent")
        return out

    SORT = {"draft_ready": 0, "reply": 1, "interview": 2, "offer": 3, "roles_found": 5, "applied": 6, "checking": 7, "not_checked": 8, "no_roles": 9,
            "site_blocked": 4, "rejected": 10, "not_interested": 11, "blocklisted": 12}

    def list(self) -> list[dict]:
        if not hasattr(self, "_uni"):
            self.refresh()
        rows = self.d.db.query("SELECT * FROM company_status")
        self._warm = self.d.intel.warm_counts()
        out = [self._view(r, self._uni.get(r["key"]), self._ev.get(r["key"])) for r in rows]
        out.sort(key=lambda x: (0 if x["unconfirmed"] else self.SORT[x["status"]] + 1, -(x["best"] if x["best"] is not None else -1), x["name"].lower()))
        return out

    def counts(self) -> dict:
        """Companies per status. Counts only: this goes into status.json."""
        return {r["status"]: r["n"] for r in self.d.db.query("SELECT status, COUNT(*) AS n FROM company_status GROUP BY status")}

    def budget(self) -> dict:
        from .service import RUN_BUDGET
        from . import sender as sender_mod
        cap_day, _h = sender_mod.caps(self.d.db)
        return {"runs_used": int(self.d.db.get(f"runs:{self.d.today()}", "0") or 0), "runs_max": RUN_BUDGET,
                "sent_today": self.d.sender.sent_today(self.d.clock()), "sent_max": cap_day}

    def page_data(self) -> dict:
        self.refresh()
        rows = self.list()
        counts = self.counts()
        funnel = {k: sum(1 for c in rows if k in c["step"]) for k in ("found", "fit", "s70", "drafted", "stopped", "ready", "sent")}
        return {"funnel": funnel, "companies": rows, "counts": counts, "statuses": [{"id": i, "label": l, "tone": TONE[i]} for i, l, _r in STATUS_LIST],
                "hand_ok": list(HAND_OK), "stages": [{"id": s, "label": l, "statuses": list(ss)} for s, l, ss in STAGES],
                "budget": self.budget(), "starter": self.d.dataset.counts()["boards"] == 0}

    def company(self, cid: int) -> dict | None:
        """The side panel: roles with scores, drafts, sent mails, replies, notes, insights (people's LinkedIn text shown as text only), history."""
        r = self._row(cid)
        if not r:
            return None
        if not hasattr(self, "_uni"):
            self.refresh()
        k, db = r["key"], self.d.db
        u, e = self._uni.get(k), self._ev.get(k)
        out = self._view(r, u, e)
        scores = self.d.scorer.table()
        from .score import TITLE_KEYWORDS, title_ok
        wanted = self.d.scorer.pref_list("prefs:titles") or list(TITLE_KEYWORDS)
        mine = [j for j in self.d.job_fetch.store.live_of(k) if key_of(j["company"]) == k]
        roles = [{"id": j["id"], "title": j["title"], "location": j["location"], "url": j["job_url"], "score": (scores.get(j["id"]) or {}).get("total"),
                  "state": (scores.get(j["id"]) or {}).get("state", ""), "why": (scores.get(j["id"]) or {}).get("why", ""),
                  "match": title_ok(j["title"], "", wanted)} for j in mine]
        roles.sort(key=lambda x: (not x["match"], -(x["score"] if x["score"] is not None else -1)))
        out["roles_total"] = len(roles)
        out["roles"] = roles[:60]
        out["drafts"] = [{"subject": o["subject"], "state": o["state"], "created": o["created"]}
                         for o in db.query("SELECT subject, state, created FROM outbox WHERE company_norm=? ORDER BY id DESC LIMIT 10", (k,))]
        out["sent"] = [{"subject": o["subject"], "sent_ts": o["sent_ts"]}
                       for o in db.query("SELECT subject, sent_ts FROM outbox WHERE company_norm=? AND state='sent' ORDER BY sent_ts DESC LIMIT 10", (k,))]
        out["warm"] = self.d.intel.warm_for(k, r["name"], (out["roles"][0]["title"] if out["roles"] else ""))
        out["replies"] = db.query("SELECT ts, subject, from_name, snippet, kind FROM company_replies WHERE key=? ORDER BY ts DESC LIMIT 10", (k,))
        out["insights"] = {h: v for h, v in ((u or {}).get("insights") or {}).items()}        # plain text; the page never turns them into links
        out["log"] = db.query("SELECT ts, status, reason, by FROM company_log WHERE key=? ORDER BY id DESC LIMIT 30", (k,))
        out["labels"] = LABEL
        out.update(self._decision(k, out))
        return out

    # ---- the company page (DJ14L): why it fits, the way to write, the email, the checks ----
    PART_LABEL = {"skills": "Skills", "seniority": "Seniority", "location": "Place and time zone", "domain": "Field", "pay": "Pay", "company": "Company"}

    def _decision(self, key: str, out: dict) -> dict:
        from .score import PARTS
        db = self.d.db
        jobs = [j for j in self.d.job_fetch.store.live() if key_of(j["company"]) == key]
        best, bs = None, None
        for j in jobs:
            s = self.d.scorer.get(j["id"])
            if s and s.get("total") is not None and (bs is None or s["total"] > bs["total"]):
                best, bs = j, s
        fit = None
        if bs:
            parts = [{"key": p, "label": self.PART_LABEL[p], "got": int(bs["parts"].get(p, 0)), "max": top,
                      "weak": int(bs["parts"].get(p, 0)) < top * 0.5} for p, top in PARTS.items()]
            fit = {"total": bs["total"], "parts": parts, "why": bs.get("why", ""), "gap": bs.get("gap", ""), "job": best["title"]}
        route = None
        if best:
            r = self.d.router.get(best["id"])
            if r:
                route = {"kind": r.get("kind"), "to": r.get("to", ""), "person": r.get("person", ""),
                         "origin": (r.get("source") or {}).get("origin", ""), "reason": r.get("reason", ""),
                         "apply_url": best["apply_url"] if r.get("kind") == "card" else ""}
        email = None
        row = db.query("SELECT * FROM outbox WHERE company_norm=? AND state IN ('waiting','held','undo','queued','sending') ORDER BY id DESC LIMIT 1", (key,))
        checks = []
        if row:
            o = row[0]
            email = {"id": o["id"], "subject": o["subject"], "body": o["body"], "to": o["to_addr"], "state": o["state"], "reason": o["reason"],
                     "kind": o["kind"], "draft_id": o["draft_id"]}
        else:
            fd = db.query("SELECT * FROM drafts WHERE state='failed' AND lower(company)=lower(?) ORDER BY id DESC LIMIT 1", (out["name"],))
            if fd:
                f = fd[0]
                email = {"id": None, "draft_id": f["id"], "subject": f["subject"], "body": f["body"], "to": f["to_addr"], "state": "failed",
                         "reason": self.d.writer.get(f["id"])["reason"], "kind": f["kind"]}
                checks = [{"word": w, "draft_id": f["id"]} for w in json.loads(f["unknown"] or "[]")]
        if email:
            used = []
            body = email["body"].lower()
            for kind_facts in self.d.cv.claimable().values():
                for fact in kind_facts:
                    toks = [t for t in re.findall(r"[a-z0-9+#.]{4,}", fact.lower())]
                    if toks and any(t in body for t in toks):
                        used.append(fact[:120])
            email.update(facts_used=used[:8], sender=self.d.login_status().get("address", ""),
                         goes_out="After you press Approve, with 10 seconds to undo. It goes out in the sending window.")
        grade = "" if not fit else "A" if fit["total"] >= 80 else "B" if fit["total"] >= 70 else "C" if fit["total"] >= 60 else ""
        return {"fit": fit, "route": route, "email": email, "checks": checks, "grade": grade}
