"""Friday's search and apply intelligence (DJ14K). Personal scope. Nothing here sends anything and nothing here visits a person.

- Title synonyms: the owner's wanted titles also match SDE-2, "member of technical staff", "ML engineer" and the like.
- Named person: a name written next to the hiring address in the post or on the company's own page (never guessed, never from LinkedIn).
- Reply sorting: invite, rejection, question, auto-reply, other. Code rules only; the text is untrusted.
- Follow-ups: day 5 and day 12 after an email application with no reply. Drafts only; the owner presses Approve on each.
- Reply drafts for an invitation (a short fixed note; the owner approves).
- Warm paths: the owner's own LinkedIn export (Connections.csv). Friday reads the file he uploads. She never opens LinkedIn.
- Learning and the weekly digest: counts per source, title and route; nothing is tuned on fewer than 30 outcomes.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta

from .dedupe import norm_company
from .domains import registrable

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (id INTEGER PRIMARY KEY, name TEXT NOT NULL, company TEXT NOT NULL, company_norm TEXT NOT NULL,
    position TEXT NOT NULL, connected_on TEXT NOT NULL, added_ts REAL NOT NULL, UNIQUE(name, company_norm, position));
CREATE INDEX IF NOT EXISTS connections_company ON connections(company_norm);
"""

# ---------------------------------------------------------------- titles
SYNONYMS = (
    ("software engineer", "software developer", "sde", "sde-1", "sde-2", "sde-3", "sde 1", "sde 2", "sde 3", "sde i", "sde ii", "sde iii", "swe",
     "member of technical staff", "mts", "programmer", "application developer", "software development engineer"),
    ("senior software engineer", "senior sde", "sr software engineer", "sr. software engineer", "sr sde", "sde iii", "sde-3", "sde 3", "senior developer",
     "senior software developer", "staff software engineer"),
    ("backend", "back end", "back-end", "server side", "server-side", "api engineer", "platform engineer", "backend engineer"),
    ("full stack", "full-stack", "fullstack", "full stack developer", "full-stack engineer", "full stack engineer"),
    ("frontend", "front end", "front-end", "ui engineer", "web developer", "web engineer"),
    ("ai engineer", "ml engineer", "machine learning engineer", "llm engineer", "applied ai", "genai engineer", "gen ai engineer", "ai/ml engineer",
     "applied ai engineer", "ai developer"),
    ("founding engineer", "founding software engineer", "first engineer", "early engineer", "founding developer"),
    ("product engineer", "software engineer, product", "product developer"),
)
_GROUP_OF: dict[str, tuple] = {}
for _g in SYNONYMS:
    for _w in _g:
        _GROUP_OF.setdefault(_w.lower(), _g)


def synonyms_of(word: str) -> list[str]:
    """The word and the words that mean the same thing in a job title. The owner's own word always comes first."""
    w = str(word or "").strip().lower()
    if not w:
        return []
    g = _GROUP_OF.get(w, ())
    return [w] + [x for x in g if x != w]


def word_in_title(word: str, title_lower: str) -> bool:
    return any(re.search(r"(?<![\w])" + re.escape(x) + r"(?![\w])", title_lower) for x in synonyms_of(word))


# ---------------------------------------------------------------- a named person
_NAME = r"[A-Z][a-z'’-]{1,20}(?:\s+[A-Z][a-z'’-]{1,20}){0,2}"
_ROLE_WORDS = r"(?:hiring\s+manager|recruiter|talent\s+(?:partner|lead|acquisition)|head\s+of\s+(?:talent|people|engineering)|cto|vp\s+engineering|engineering\s+manager|founder)"
_NOT_NAMES = {"Email", "Send", "Apply", "Please", "Contact", "Mail", "Your", "Our", "The", "Hiring", "Team", "Jobs", "Careers", "Talent", "Hr", "Hello", "Hi", "Dear",
              "Office", "Offices", "Bangalore", "Bengaluru", "Mumbai", "Delhi", "Pune", "Chennai", "Hyderabad", "Gurgaon", "Noida", "London", "Remote",
              "Headquarters", "Hq", "Recruitment", "Recruiting", "Recruiter", "Support", "Info", "Sales", "Admin", "People", "Staff", "Engineering",
              "Careers", "Human", "Resources", "Group", "Department", "Desk", "Careers", "Application", "Applications", "Reply", "Noreply"}


def looks_like_name(word: str) -> bool:
    """One word that can be a person's first name: letters only, one capital, not a place, team or role word."""
    return bool(re.fullmatch(r"[A-Z][a-z'’-]{1,20}", word or "")) and word not in _NOT_NAMES


def _shares_letters(name: str, address: str) -> bool:
    local = re.sub(r"[^a-z]", "", address.split("@")[0].lower())
    parts = [re.sub(r"[^a-z]", "", w.lower()) for w in name.split()]
    first, last = parts[0], parts[-1]
    return bool(local) and ((len(first) >= 3 and first in local) or (len(last) >= 3 and last in local) or (len(parts) > 1 and local.startswith(first[0] + last)))


def person_in_context(context: str, address: str) -> str:
    """The first name of a person written next to the address in a post or page: "Priya Sharma (priya@x.com)", "email Priya at priya@x.com",
    "Hiring manager: Priya Sharma, priya@x.com". Empty when no name is written there. Never guessed from the address."""
    text = " ".join((context or "").split())
    if not text or not address:
        return ""
    i = text.lower().find(address.lower())
    if i < 0:
        return ""
    window = text[max(0, i - 90):i + len(address) + 40]
    cands = []
    for m in re.finditer(r"(" + _NAME + r")\s*[\(<\[,:-]\s*" + re.escape(address), window):
        if _shares_letters(m.group(1), address) and not any(w in _NOT_NAMES for w in m.group(1).split()):   # "Bangalore Office: jobs@" names no one
            cands.append(m.group(1))
    for m in re.finditer(r"(?i:" + _ROLE_WORDS + r")\s*[:\-–,]?\s*(" + _NAME + ")", window):
        cands.append(m.group(1))
    for m in re.finditer(r"\b(?i:email|write\s+to|contact|reach|ask\s+for|talk\s+to)\s+(" + _NAME + r")\s+(?i:at|on|via)\b", window):
        cands.append(m.group(1))
    for c in cands:
        first = c.split()[0]
        if looks_like_name(first) and not any(w in _NOT_NAMES for w in c.split()):
            return first
    return ""


def _connected_date(text: str) -> date:
    """LinkedIn writes "12 Mar 2021". Text order is not date order, so the date is read before sorting. Unreadable is oldest."""
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%b %d, %Y", "%d/%m/%Y", "%d %B %Y"):
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return date.min


# ---------------------------------------------------------------- replies
_AUTO_STRONG = re.compile(r"auto[-\s]?reply|automatic(?:ally)?\s+(?:reply|response|generated)|out\s+of\s+(?:the\s+)?office|on\s+(?:annual\s+)?leave|"
                          r"this\s+is\s+an?\s+automated|do\s+not\s+reply\s+to\s+this", re.I)
_ACK = re.compile(r"we(?:'ve|\s+have)\s+received\s+your\s+(?:application|email)|thank\s+you\s+for\s+(?:applying|your\s+application)|"
                  r"your\s+application\s+has\s+been\s+received", re.I)
_REJECT = re.compile(r"unfortunately|regret\s+to\s+(?:inform|tell)|we(?:'ve|\s+have)\s+decided\s+to\s+(?:move|proceed|go)\s+(?:forward\s+)?with\s+(?:other|another)|"
                     r"not\s+(?:be\s+)?(?:moving|proceeding)\s+forward|will\s+not\s+be\s+(?:moving|proceeding)|position\s+(?:has\s+been|is)\s+filled|"
                     r"not\s+(?:been\s+)?selected|unable\s+to\s+(?:offer|move)|decided\s+not\s+to\s+(?:move|proceed)|other\s+candidates", re.I)
_INVITE = re.compile(r"\b(?:schedule|set\s+up|book|arrange)\b[^.!?]{0,60}\b(?:call|chat|interview|meeting|conversation|time)\b|"
                     r"\b(?:invite|invitation|like\s+to\s+(?:talk|speak|meet|chat)|would\s+love\s+to\s+(?:talk|speak|meet|chat)|"
                     r"available\s+(?:for|on)\b[^.!?]{0,40}\b(?:call|chat|interview)|are\s+you\s+free|let\s+us\s+know\s+(?:a\s+)?(?:time|your\s+availability)|"
                     r"your\s+availability|calendly|first\s+round|technical\s+(?:round|interview)|phone\s+screen)\b", re.I)
# Wording that only promises a call under a condition: "will schedule a call if your profile matches", "will be in touch" (DJ14KF M4).
_CONDITIONAL = re.compile(r"\bif\b|\bshould\b|\bwhen\s+(?:suitable|appropriate)\b|\bin\s+case\b|\bmay\b|\bmight\b|\bwill\s+be\s+in\s+touch\b|\bwill\s+(?:contact|reach\s+out)\b", re.I)
# A direct ask stays an invite even when the sentence has an "if": "If you are free on Thursday, please say so."
_DIRECT_ASK = re.compile(r"are\s+you\s+free|your\s+availability|calendly|let\s+us\s+know\s+(?:a\s+)?(?:time|your\s+availability)|"
                         r"\b\d{1,2}(?::\d\d)?\s?(?:am|pm)\b|\b(?:monday|tuesday|wednesday|thursday|friday)\b", re.I)
_QUESTION = re.compile(r"\?|\bcould\s+you\s+(?:please\s+)?(?:send|share|tell|confirm|provide)\b|\bplease\s+(?:send|share|confirm|provide)\b|"
                       r"\bwhat\s+is\s+your\b|\bnotice\s+period\b|\bexpected\s+(?:salary|ctc)\b|\bcurrent\s+ctc\b", re.I)


def _is_invite(t: str) -> bool:
    """A real invitation: invite wording in a sentence that does not only promise a call under a condition."""
    for sent in re.split(r"(?<=[.!?])\s+|\n+", t):
        if _INVITE.search(sent) and (_DIRECT_ASK.search(sent) or not _CONDITIONAL.search(sent)):
            return True
    return False


def classify_reply(text: str, subject: str = "") -> str:
    """auto, rejection, invite, question or reply. A plain automatic reply (out of office) is auto; then rejection wording wins over
    everything (a polite "Thank you for your application. Unfortunately ..." is a rejection that the owner confirms); then a real
    invite; then an automatic acknowledgement; then a question."""
    t = f"{subject}\n{text}"
    if _AUTO_STRONG.search(t):
        return "auto"
    if _REJECT.search(t):
        return "rejection"
    if _is_invite(t):
        return "invite"
    if _ACK.search(t):
        return "auto"
    if _QUESTION.search(t):
        return "question"
    return "reply"


INVITE_REPLY = ("Hi {first},\n\nThank you for your reply. I would be glad to talk.{where} Please tell me a time that suits you, "
                "and I will confirm it.\n")


def invite_reply_text(d, first: str, sign: str) -> str:
    h = d.home() if d is not None else None
    place = (h.tz_label or h.country) if h else ""          # from the person's own settings; nothing is assumed
    where = f" I work in {place}." if place else ""
    body = INVITE_REPLY.format(first=first, where=where) if looks_like_name(first) else INVITE_REPLY.replace("Hi {first},", "Hello,").format(where=where)
    return body + ("\n" + sign.strip() if sign and sign.strip() else "")


# ---------------------------------------------------------------- follow-ups and reply drafts
FOLLOWUP_DAYS = (5, 12)
FOLLOWUP_TEXT = (
    "Hi {team},\n\nI wrote on {when} about the {title} role. I am still interested, and I am happy to answer any question about my work. "
    "Is the role still open?\n",
    "Hi {team},\n\nThis is a short second note about the {title} role. I know hiring takes time. If it helps, I can send more detail about "
    "my recent work. If the role is filled, no reply is needed.\n")


class Intel:
    def __init__(self, donna):
        self.d = donna

    # ---- follow-ups ----
    def _sign(self) -> str:
        try:
            return self.d.writer.sign_off()
        except Exception:  # noqa: BLE001: no sign-off yet: the note still reads fine without one
            return ""

    def followups(self) -> dict:
        """Create follow-up drafts that are due. Drafts only: they join the list "Waiting for you" and nothing goes out by itself."""
        db, now = self.d.db, self.d.clock()
        made = 0
        sent = db.query("SELECT * FROM outbox WHERE state='sent' AND kind IN ('application','speculative') AND sent_ts IS NOT NULL ORDER BY id")
        drafts = db.query("SELECT id, source, state FROM drafts WHERE kind='followup'")
        by_origin: dict[int, list] = {}
        for dr in drafts:
            by_origin.setdefault(int((json.loads(dr["source"] or "{}")).get("origin_outbox") or 0), []).append(dr)
        out_by = {}
        for o in db.query("SELECT id, state, source FROM outbox WHERE kind='followup'"):
            out_by.setdefault(int((json.loads(o["source"] or "{}")).get("origin_outbox") or 0), []).append(o)
        for o in sent:
            if self.d.is_paused():
                break
            days = (now - o["sent_ts"]) / 86400
            have = by_origin.get(o["id"], [])
            step = len(have)
            if step >= len(FOLLOWUP_DAYS) or days < FOLLOWUP_DAYS[step]:
                continue
            if step == 1 and not any(x["state"] == "sent" for x in out_by.get(o["id"], [])):
                continue                          # the second note only follows a first note the owner sent
            if db.query("SELECT 1 FROM company_replies WHERE key=?", (o["company_norm"],)):
                continue
            if db.query("SELECT 1 FROM company_status WHERE key=? AND status IN ('rejected','not_interested','interview','offer','blocklisted')", (o["company_norm"],)):
                continue
            if any(x["state"] == "waiting" for x in have):
                continue
            title = (db.query("SELECT title FROM drafts WHERE id=?", (o["draft_id"],)) or [{"title": ""}])[0]["title"] or "open"
            when = date.fromtimestamp(o["sent_ts"]).strftime("%d %B")
            team = f"{o['company']} team" if o["company"] else "team"
            body = FOLLOWUP_TEXT[step].format(team=team, when=when, title=title)
            sign = self._sign()
            if sign:
                body += "\n" + sign.strip()
            subject = o["subject"] if o["subject"].lower().startswith("re:") else "Re: " + o["subject"]
            with db.tx() as c:
                c.execute("INSERT INTO drafts(kind, channel, job_id, company_id, company, title, to_addr, source, subject, body, state, ts) "
                          "VALUES('followup', 'email', NULL, NULL, ?, ?, ?, ?, ?, ?, 'waiting', ?)",
                          (o["company"], title, o["to_addr"], json.dumps({"origin_outbox": o["id"], "step": step + 1}), subject[:200], body, now))
            db.event("followup", f"Follow-up {step + 1} drafted for {o['company']}", self.d.clock)
            made += 1
        return {"made": made}

    def reply_drafts(self) -> int:
        """A short reply for each invitation, as a draft. Only to a sender on the company's own site. Questions get a task, not a draft.
        Stop rules (DJ14KF): none after a rejection or when the company is closed; one waiting reply per thread; an exact duplicate
        check by message id (read from the stored JSON, never matched with LIKE); none for a free-mail or shared first address."""
        from .route import WEBMAIL, is_hiring_system
        db, now, made = self.d.db, self.d.clock(), 0
        rows = db.query("SELECT * FROM company_replies WHERE kind='invite' AND outbox_id IS NOT NULL AND ts > ? ORDER BY ts", (now - 14 * 86400,))
        for r in rows:
            made_for = [json.loads(x["source"] or "{}") for x in db.query("SELECT source FROM drafts WHERE kind='reply'")]
            if any(str(x.get("reply_id")) == r["message_id"] for x in made_for):
                continue
            o = db.query("SELECT * FROM outbox WHERE id=?", (r["outbox_id"],))
            addr = (r.get("from_addr") if isinstance(r, dict) else "") or ""
            if not o or not addr or "@" not in o[0]["to_addr"] or registrable(addr.rsplit("@", 1)[-1]) != registrable(o[0]["to_addr"].rsplit("@", 1)[-1]):
                continue
            origin = o[0]["to_addr"].rsplit("@", 1)[-1].lower()
            if origin in WEBMAIL or registrable(origin) in WEBMAIL or is_hiring_system(origin):
                continue                              # a shared address stands for many people: no automatic reply draft
            key = o[0]["company_norm"]
            if db.query("SELECT 1 FROM company_status WHERE key=? AND status IN ('rejected','not_interested','blocklisted')", (key,)) or \
                    db.query("SELECT 1 FROM company_replies WHERE key=? AND kind='rejection'", (key,)):
                continue
            if any(int(x.get("origin_outbox") or 0) == o[0]["id"] for x in [json.loads(y["source"] or "{}")
                                                                          for y in db.query("SELECT source FROM drafts WHERE kind='reply' AND state='waiting'")]):
                continue                              # one waiting reply per thread
            first = (r["from_name"] or "").split()[0] if r["from_name"] else ""
            body = invite_reply_text(self.d, first, self._sign())
            subject = r["subject"] if r["subject"].lower().startswith("re:") else "Re: " + r["subject"]
            with db.tx() as c:
                c.execute("INSERT INTO drafts(kind, channel, job_id, company_id, company, title, to_addr, source, subject, body, state, ts) "
                          "VALUES('reply', 'email', NULL, NULL, ?, '', ?, ?, ?, ?, 'waiting', ?)",
                          (o[0]["company"], addr, json.dumps({"origin_outbox": o[0]["id"], "reply_to": addr, "reply_id": r["message_id"]}),
                           subject[:200], body, now))
            db.event("followup", f"Reply drafted for the invitation from {o[0]['company']}", self.d.clock)
            made += 1
        return made

    def withdraw_replies(self, key: str) -> None:
        """A rejection came in: reply drafts and unsent reply emails for this company are withdrawn (DJ14KF M3)."""
        db = self.d.db
        ids = [x["id"] for x in db.query("SELECT id FROM outbox WHERE company_norm=?", (key,))]
        with db.tx() as c:
            for dr in c.execute("SELECT id, source FROM drafts WHERE kind='reply' AND state='waiting'").fetchall():
                if int((json.loads(dr["source"] or "{}")).get("origin_outbox") or 0) in ids:
                    c.execute("UPDATE drafts SET state='rejected' WHERE id=?", (dr["id"],))
            for o in c.execute("SELECT id FROM outbox WHERE kind='reply' AND state IN ('waiting','held','undo','queued') AND company_norm=?", (key,)).fetchall():
                c.execute("UPDATE outbox SET state='rejected', reason='The company said no. The reply was withdrawn.' WHERE id=?", (o["id"],))

    # ---- warm paths ----
    def import_connections(self, data) -> dict:
        """The owner's own LinkedIn export. Only name, company, position and date are kept. No address, no link, no email."""
        text = data.decode("utf-8-sig", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
        if len(text) > 5 * 1024 * 1024:
            return {"ok": False, "error": "The file is larger than 5 MB."}
        try:
            rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
        except csv.Error:
            return {"ok": False, "error": "A cell in this file is too long (more than 128 KB), so it is not LinkedIn's Connections.csv."}
        hi = next((i for i, r in enumerate(rows[:15]) if "first name" in [c.strip().lower() for c in r] and "company" in [c.strip().lower() for c in r]), None)
        if hi is None:
            return {"ok": False, "error": "This does not look like LinkedIn's Connections.csv. It needs the columns First Name, Last Name and Company."}
        head = [c.strip().lower() for c in rows[hi]]
        col = {k: head.index(k) for k in ("first name", "last name", "company", "position", "connected on") if k in head}
        added = 0
        with self.d.db.tx() as c:
            c.execute("DELETE FROM connections")                  # each upload replaces the list: people who are no longer connections go
            for r in rows[hi + 1:]:
                g = lambda k: r[col[k]].strip()[:120] if k in col and col[k] < len(r) else ""   # noqa: E731
                name = (g("first name") + " " + g("last name")).strip()
                comp = g("company")
                if not name or not comp:
                    continue
                cur = c.execute("INSERT OR IGNORE INTO connections(name, company, company_norm, position, connected_on, added_ts) VALUES(?,?,?,?,?,?)",
                                (name, comp, norm_company(comp), g("position"), g("connected on"), self.d.clock()))
                added += cur.rowcount
        self.d.db.event("warm", f"Connections file read: {added} people kept", self.d.clock)
        return {"ok": True, "added": added, "total": self.connection_count()}

    def clear_connections(self) -> dict:
        """The owner removes the whole list."""
        with self.d.db.tx() as c:
            n = c.execute("DELETE FROM connections").rowcount
        self.d.db.event("warm", f"Connections removed: {n} people", self.d.clock)
        return {"ok": True, "removed": n, "total": 0}

    def connection_count(self) -> int:
        return self.d.db.query("SELECT COUNT(*) AS n FROM connections")[0]["n"]

    def warm_counts(self) -> dict[str, int]:
        return {r["company_norm"]: r["n"] for r in self.d.db.query("SELECT company_norm, COUNT(*) AS n FROM connections GROUP BY company_norm")}

    def warm_for(self, company_norm: str, company: str, role: str = "") -> dict:
        people = sorted(self.d.db.query("SELECT name, position, connected_on FROM connections WHERE company_norm=?", (company_norm,)),
                        key=lambda p: _connected_date(p["connected_on"]), reverse=True)[:10]
        out = {"people": people, "ask": ""}
        if people:
            first = people[0]["name"].split()[0]
            sign = self._sign()
            what = f"the {role} role" if role else "an engineering role"
            out["ask"] = (f"Hi {first},\n\nI saw that {company} is hiring for {what}. I would like to apply. Would you be open to a short chat, "
                          "or to pass my CV to the right person? I can send a short summary that you can forward.\n" + ("\n" + sign.strip() if sign else ""))
        return out

    # ---- learning and the digest ----
    def outcomes(self) -> dict:
        """Per source, per title kind and per route: applied, replies, interviews. Counts only. `enough` is true from 30 resolved outcomes."""
        db = self.d.db
        rows = db.query("SELECT o.id, o.company_norm, o.job_id, o.source, o.draft_id, o.sent_ts FROM outbox o WHERE o.state='sent' AND o.kind IN ('application','speculative')")
        replies = {r["key"]: r for r in db.query("SELECT key, kind FROM company_replies ORDER BY ts")}
        invites = {r["key"] for r in db.query("SELECT key FROM company_replies WHERE kind='invite'")}
        talk = {r["key"] for r in db.query("SELECT key FROM company_status WHERE status IN ('interview','offer')")} | invites
        jobs = {j["id"]: j for j in self.d.job_fetch.store.all()}
        dims: dict[str, dict[str, dict]] = {"source": {}, "title": {}, "route": {}}
        now = self.d.clock()
        resolved = 0
        for o in rows:
            job = jobs.get(o["job_id"]) if o["job_id"] else None
            src = (job or {}).get("source", "speculative") or "speculative"
            t = ((job or {}).get("title") or "").lower()
            kind = "ai" if re.search(r"\bai\b|\bml\b|machine learning|llm", t) else "backend" if re.search(r"back[\s-]?end|platform|api", t) else \
                "full stack" if re.search(r"full[\s-]?stack", t) else "frontend" if re.search(r"front[\s-]?end|react|ui", t) else "other"
            route = (json.loads(o["source"] or "{}") or {}).get("origin", "unknown")
            got = o["company_norm"] in replies
            talked = o["company_norm"] in talk
            if got or (now - (o["sent_ts"] or now)) > 14 * 86400:
                resolved += 1
            for dim, key in (("source", src), ("title", kind), ("route", route)):
                c = dims[dim].setdefault(key, {"applied": 0, "replies": 0, "interviews": 0})
                c["applied"] += 1
                c["replies"] += int(got)
                c["interviews"] += int(talked)
        return {"applied": len(rows), "resolved": resolved, "enough": resolved >= 30, "need": 30,
                "by": {k: sorted(({"name": n, **v} for n, v in m.items()), key=lambda x: (-x["interviews"], -x["replies"], -x["applied"])) for k, m in dims.items()}}

    def digest(self) -> dict:
        """The week at a glance and the next week's top 10 roles. No model run."""
        db, now = self.d.db, self.d.clock()
        week = now - 7 * 86400
        applied = db.query("SELECT COUNT(*) AS n FROM outbox WHERE state='sent' AND kind IN ('application','speculative') AND sent_ts>=?", (week,))[0]["n"]
        followups = db.query("SELECT COUNT(*) AS n FROM outbox WHERE state='sent' AND kind='followup' AND sent_ts>=?", (week,))[0]["n"]
        reps = db.query("SELECT kind, COUNT(*) AS n FROM company_replies WHERE ts>=? GROUP BY kind", (week,))
        rk = {r["kind"]: r["n"] for r in reps}
        interviews = db.query("SELECT COUNT(DISTINCT key) AS n FROM company_log WHERE status IN ('interview','offer') AND ts>=?", (week,))[0]["n"]
        outc = self.outcomes()
        best = []
        if outc["enough"]:
            best = [x for x in outc["by"]["source"] if x["interviews"]][:3]
        applied_keys = {a["company_norm"] for a in db.query("SELECT company_norm FROM applied")}
        scores = self.d.scorer.table()
        top = []
        for j in self.d.job_fetch.store.live():
            s = scores.get(j["id"]) or {}
            if s.get("total") is None or s["total"] < 50 or j["company_norm"] in applied_keys:
                continue
            top.append({"company": j["company"], "title": j["title"], "score": s["total"], "state": s.get("state", "")})
        top.sort(key=lambda x: -x["score"])
        return {"week_from": date.fromtimestamp(week).isoformat(), "applied": applied, "followups": followups, "replies": sum(rk.values()),
                "reply_kinds": rk, "interviews": interviews, "best_sources": best,
                "learning": {"enough": outc["enough"], "resolved": outc["resolved"], "need": outc["need"]}, "top": top[:10]}
