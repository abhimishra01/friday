"""The apply route (DJ05, PLAN-FINAL rows 38 and 39, JOB-SOURCES 4.4 and 4.5): an email, or an "apply via form" card.

An email goes only to an address published for hiring or contact:
1. in the post, next to words about applying: its domain must equal the company's domain or a known hiring-system domain.
   A webmail address (gmail.com and the like) makes a card; so does a domain that does not match. HN posts count as posts;
2. careers@, jobs@, hiring@, talent@ or recruiting@ on the company's own careers or contact page (read by the fetch worker,
   only for a role with no form of its own, and for the speculative kind);
3. a hiring manager's public post that asks for emails (the same checks as a post).
Every address keeps the address of the page it came from and the date, and the draft shows both. Otherwise the role gets an
"apply via form" card: job link, score, the cover note to paste (the writer, DJ06), answers to common questions, "Mark as
applied". Friday never guesses an address, never probes a mail server and never uses a finder.
"Apply via the form" and "do not email" notes always make a card. "No recruiters" and "no agencies" notes go on the draft
(the owner applies for himself, so the writer must not sound like an agency). An HN subject format goes to the writer.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from .blocklist import board_tokens, compact
from .dedupe import norm_company
from .domains import host_of, registrable
from .sources.careers import ROLE_LOCALS
from .worker import never_fetch

# Hosts of job boards and other sites that are not the company's own (a link there tells us nothing about the domain).
NOT_COMPANY_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com", "recruitee.com",
                     "remoteok.com", "weworkremotely.com", "remotive.com", "himalayas.app", "arbeitnow.com", "ycombinator.com",
                     "linkedin.com", "indeed.com", "naukri.com", "wellfound.com", "angel.co", "upwork.com", "contra.com", "jooble.org",
                     "github.com", "gitlab.com", "notion.so", "notion.site", "google.com", "forms.gle", "typeform.com", "airtable.com",
                     "bit.ly", "medium.com", "substack.com", "twitter.com", "x.com", "teamtailor.com", "bamboohr.com", "breezy.hr",
                     "applytojob.com", "jobvite.com", "icims.com", "personio.de", "personio.com", "homerun.co", "workablemail.com")
# Hiring systems that give each company an apply-by-email address on their own domain.
HIRING_SYSTEM_DOMAINS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workablemail.com", "workable.com", "recruitee.com",
                         "smartrecruiters.com", "teamtailor.com", "bamboohr.com", "breezy.hr", "applytojob.com", "jazzhr.com",
                         "homerun.co", "personio.de", "personio.com", "jobvite.com", "icims.com", "freshteam.com", "zohorecruit.com")
WEBMAIL = ("gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk", "ymail.com", "rocketmail.com", "outlook.com",
           "hotmail.com", "hotmail.co.uk", "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
           "protonmail.com", "pm.me", "gmx.com", "gmx.de", "gmx.net", "mail.com", "yandex.com", "yandex.ru", "zoho.com", "zohomail.com",
           "rediffmail.com", "qq.com", "163.com", "126.com", "tutanota.com", "tuta.io", "hey.com", "fastmail.com", "fastmail.fm",
           "inbox.com", "mail.ru", "web.de", "hushmail.com", "duck.com")
# An address that is not for job applications: accessibility, accommodation, disability, privacy, legal, abuse, security, press,
# support, no-reply (DJ14G). Judged by the local part and, for an address in a post, by the words around it.
_NOT_HIRING_LOCAL = re.compile(r"accessib|accommodat|disabilit|privacy|legal|abuse|security|noreply|donotreply|unsubscribe", re.I)
_NOT_HIRING_TOKENS = {"press", "media", "support", "helpdesk", "dpo", "gdpr", "compliance", "a11y", "psirt"}
_NOT_HIRING_NEAR = re.compile(r"accessib|accommodat|disabilit|privacy|data\s+protection|legal\s+(?:notice|request|question|team)|abuse|"
                              r"security\s+(?:team|issue|concern|report|vulnerabilit)|vulnerabilit|press\s+(?:inquir|enquir|contact)|"
                              r"media\s+(?:inquir|enquir)|support\s+(?:request|team|question)|customer\s+support|"
                              r"no[\s-]?reply|do\s+not\s+reply", re.I)
MAX_PAGES = 3
COOL_OFF_DAYS = 60

COMMON_QUESTIONS = (
    ("notice", "What is your notice period?"),
    ("salary", "What are your salary expectations?"),
    ("location", "Where are you based?"),
    ("timezone", "Which time zone do you work in?"),
    ("authorisation", "Do you need a visa or sponsorship?"),
    ("years", "How many years of experience do you have?"),
    ("why", "Why do you want to work here?"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_routes (job_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, route TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS speculative (company_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, route TEXT NOT NULL, ts REAL NOT NULL);
"""


def _under(domain: str, roots) -> bool:
    d = (domain or "").lower()
    return any(d == r or d.endswith("." + r) for r in roots)


def is_webmail(domain: str) -> bool:
    return _under(domain, WEBMAIL)


def is_hiring_system(domain: str) -> bool:
    return _under(domain, HIRING_SYSTEM_DOMAINS)


def not_for_hiring(address: str, context: str = "") -> str:
    """Empty when the address may get a job application; else the plain reason it may not. Never write to an accessibility,
    accommodation, privacy, legal, abuse, security, press, support or no-reply address."""
    local = address.lower().rpartition("@")[0]
    tokens = set(re.split(r"[^a-z0-9]+", local))
    if _NOT_HIRING_LOCAL.search(local.replace("-", "").replace("_", "").replace(".", "")) or tokens & _NOT_HIRING_TOKENS:
        return f"The address {address} is for accessibility, privacy, legal, security, press or support, not for job applications."
    if context and _NOT_HIRING_NEAR.search(context):
        return f"The text next to {address} says it is for accessibility, privacy, legal, security, press or support, not for job applications."
    return ""


def company_site(url: str) -> str:
    """The registrable domain of a link, or empty when the link points to a job board or another shared site."""
    h = host_of(url)
    return "" if not h or _under(h, NOT_COMPANY_HOSTS) or never_fetch(h) else registrable(h)


def belongs_to_company(address: str, company_norm: str, tokens=()) -> bool:
    """A hiring-system address (one domain for many companies) is this company's only when its local part or its subdomain holds
    the company's name or its job-board token (DJ10 M4). The domain alone says nothing about the company."""
    local, _, host = address.lower().rpartition("@")
    words = {compact(x) for x in [local, *host.split(".")[:-2]] if compact(x)}
    names = {compact(company_norm)} | {compact(t) for t in tokens}
    names = {n for n in names if len(n) >= 3}
    return any(n == w or (len(n) >= 4 and n in w) for n in names for w in words)


def check_address(address: str, company_domain: str, company_norm: str = "", tokens=(), context: str = "") -> str:
    """Empty when the address may be used for this company; else the reason it makes a card."""
    bad = not_for_hiring(address, context)
    if bad:
        return bad
    domain = address.rsplit("@", 1)[-1].lower()
    if is_webmail(domain):
        return f"The post gives a webmail address ({address}), not a company address."
    if is_hiring_system(domain):
        if belongs_to_company(address, company_norm, tokens):
            return ""
        return f"The address {address} is on a hiring system that many companies share, and nothing in it names this company."
    if not company_domain:
        return f"Friday does not know the company's own domain, so she cannot check {address}."
    if registrable(domain) != registrable(company_domain):
        return f"The address {address} is not on the company's domain ({company_domain})."
    return ""


def notes_for_draft(notes: dict) -> list[str]:
    out = []
    if notes.get("no_recruiters"):
        out.append("The post says no recruiters. You apply for yourself, so the note must not read like a recruiter's.")
    if notes.get("no_agencies"):
        out.append("The post says no agencies. You apply for yourself, so the note must not read like an agency's.")
    if notes.get("no_sponsorship"):
        out.append("The post says no visa sponsorship.")
    if notes.get("hidden_removed"):
        out.append(f"Friday removed {notes['hidden_removed']} hidden part(s) of the post.")
    return out


def plan(facts: dict, company_domain: str, pages: list[dict] | None = None, public_posts: list[dict] | None = None) -> dict:
    """The route for one role, from facts and already-read pages. No network here.

    pages: the careers parser's answers, each {"url", "date", "addresses", "form_only", ...}; public_posts: {"url", "date",
    "emails"} with `emails` in the postfacts shape. Returns {"kind": "email", "to", "source": {...}} or {"kind": "card", "reason"}.
    """
    notes = facts.get("notes") or {}
    base = {"subject_format": facts.get("subject_format", ""), "draft_notes": notes_for_draft({**notes, "hidden_removed": facts.get("hidden_removed", 0)}),
            "company_domain": company_domain}
    if notes.get("form_only"):
        return {**base, "kind": "card", "reason": "The post asks you to apply via the form, not by email."}
    reasons: list[str] = []
    origin = "hn_post" if facts.get("source") == "hn" else "post"
    tokens = board_tokens(facts.get("job_url", "")) | board_tokens(facts.get("apply_url", ""))
    cnorm = facts.get("company_norm") or norm_company(facts.get("company", ""))
    for e in facts.get("emails") or []:
        if not e.get("hiring"):
            continue
        why = check_address(e["address"], company_domain, cnorm, tokens, e.get("context", ""))
        if why:
            reasons.append(why)
            continue
        from .intel import person_in_context
        return {**base, "kind": "email", "to": e["address"], "person": person_in_context(e.get("context", ""), e["address"]),
                "source": {"origin": origin, "url": facts.get("job_url", ""), "date": facts.get("posted") or facts.get("first_seen", ""),
                           "context": e.get("context", "")[:240], "written": e.get("written", "plain")}}
    if reasons:                       # the post gave an address Donna may not use: the form is the safe way
        return {**base, "kind": "card", "reason": reasons[0]}
    for p in pages or []:
        if p.get("form_only"):
            return {**base, "kind": "card", "reason": "The company's page asks you to apply via the form, not by email."}
        if p.get("instructions") or not company_domain or registrable(host_of(p.get("url", ""))) != registrable(company_domain):
            continue                   # a page with model-aimed instructions, or a page off the company's site, gives no address
        for addr in p.get("addresses") or []:
            if addr.split("@", 1)[0] in ROLE_LOCALS and not check_address(addr, company_domain) and not is_hiring_system(addr.split("@")[-1]):
                kind = "contact_page" if re.search(r"contact", urlsplit(p["url"]).path or "", re.I) else "careers_page"
                return {**base, "kind": "email", "to": addr, "source": {"origin": kind, "url": p["url"], "date": p.get("date", "")}}
    for post in public_posts or []:
        for e in post.get("emails") or []:
            if e.get("hiring") and not check_address(e["address"], company_domain, cnorm, tokens, e.get("context", "")):
                return {**base, "kind": "email", "to": e["address"],
                        "source": {"origin": "public_post", "url": post.get("url", ""), "date": post.get("date", ""),
                                   "context": e.get("context", "")[:240]}}
    return {**base, "kind": "card", "reason": "No address published for hiring. Apply with the form."}


class Router:
    def __init__(self, donna, runner=None):
        self.d = donna
        self.runner = runner

    def today(self) -> str:
        return self.d.today()

    # ---- the company's own domain ----
    def company_domain(self, facts: dict) -> str:
        cn = facts.get("company_norm") or norm_company(facts.get("company", ""))
        if cn:
            for c in self.d.dataset.companies():
                if c["domain"] and norm_company(c["name"]) == cn:
                    return registrable(c["domain"])
        for url in (facts.get("apply_url", ""), facts.get("job_url", "")):
            d = company_site(url)
            if d:
                return d
        return ""

    # ---- pages, read by the worker ----
    def _worker(self, req: dict) -> dict:
        if self.runner:
            return self.runner(req)
        return self.d.run_worker(req, timeout=30 + 50 * len(req.get("tasks") or []))

    def read_pages(self, domain: str, start_urls: list[str]) -> list[dict]:
        """The careers parser on the company's own pages: the start pages, then up to MAX_PAGES careers or contact pages they link."""
        if not domain or self.d.is_paused() or self.d.sheets.is_blocked_today(domain):
            return []
        out: list[dict] = []
        queue = [u for u in start_urls if registrable(host_of(u)) == domain]
        seen: set[str] = set()
        while queue and len(seen) < MAX_PAGES:
            batch = [u for u in queue if u not in seen][:MAX_PAGES - len(seen)]
            queue = []
            if not batch:
                break
            seen.update(batch)
            reply = self._worker({"op": "jobs", "user_agent": self.d.job_fetch.user_agent(), "pause": 1.0,
                                  "tasks": [{"id": str(i), "parser": "careers", "url": u, "ctx": {}} for i, u in enumerate(batch)]})
            for r in reply.get("results") or [] if reply.get("ok") else []:
                if isinstance(r, dict) and r.get("blocked"):
                    self.d.sheets.note_blocked(domain)       # the site blocks robots: skipped for the day (DJ14H)
                if not isinstance(r, dict) or not r.get("ok") or r.get("status") != 200:
                    continue
                rid = str(r.get("id", ""))
                url = str(r.get("final_url") or (batch[int(rid)] if rid.isdigit() and int(rid) < len(batch) else ""))
                meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
                if registrable(host_of(url)) != domain:
                    continue                   # a redirect off the company's site: nothing from there counts
                out.append({**meta, "url": url, "date": self.today()})
                queue += [p for p in meta.get("pages") or [] if isinstance(p, str) and registrable(host_of(p)) == domain and p not in seen]
        return out

    # ---- applications ----
    def route(self, facts: dict, score: dict | None = None) -> dict:
        domain = self.company_domain(facts)
        has_form = not company_site(facts.get("apply_url", "")) and bool(facts.get("apply_url"))
        pages = None
        hiring_in_post = any(e.get("hiring") for e in facts.get("emails") or [])
        if domain and not hiring_in_post and not facts["notes"].get("form_only") and not has_form:
            pages = self.read_pages(domain, [facts["apply_url"]] if company_site(facts["apply_url"]) else [f"https://{domain}/"])
        r = plan(facts, domain, pages)
        bl = self.d.blocklist
        if bl.company_blocked(facts.get("company", ""), [facts.get("job_url", ""), facts.get("apply_url", "")], [r["to"]] if r["kind"] == "email" else []):
            r = {**r, "kind": "card", "reason": "This company or address is on your blocklist."}
            r.pop("to", None)
            r.pop("source", None)
        if r["kind"] == "card":
            r["card"] = self.card(facts, score, r["reason"])
        self.store("job_routes", "job_id", facts["job_id"], r)
        return r

    def card(self, facts: dict, score: dict | None, reason: str) -> dict:
        return {"title": facts["title"], "company": facts["company"], "job_url": facts["job_url"], "apply_url": facts["apply_url"],
                "score": (score or {}).get("total"), "why": (score or {}).get("why", ""), "gap": (score or {}).get("gap", ""),
                "reason": reason, "cover_note": "", "cover_note_from": "",
                "answers": self.answers(facts), "actions": ["Mark as applied"]}

    def answers(self, facts: dict) -> list[dict]:
        """Answers to common form questions, from the owner's settings (prefs:answers) and the CV facts he ticked. Blank means: add it."""
        mine = self.d.db.get_json("prefs:answers", {}, dict)
        years = (self.d.cv.claimable().get("years") or [""])[0]
        h = self.d.home()
        defaults = {"timezone": h.tz_label, "years": years, "salary": self.d.db.get("prefs:salary_floor", ""),
                    "location": h.country}
        out = []
        for key, q in COMMON_QUESTIONS:
            a = str(mine.get(key) or defaults.get(key) or "")[:300]
            out.append({"key": key, "question": q, "answer": a, "missing": not a})
        return out

    def reroute_not_hiring(self) -> list[int]:
        """Roles whose email route goes to an address that is not for hiring become a card (DJ14G). An email draft that waits for the
        owner for such a role is withdrawn, and its outbox row is refused unless it is already sending or sent. Returns the job ids."""
        from .postfacts import extract
        changed: list[int] = []
        jobs = {j["id"]: j for j in self.d.job_fetch.store.all()}
        for row in self.d.db.query("SELECT job_id, route FROM job_routes WHERE kind='email'"):
            r = json.loads(row["route"])
            src = r.get("source") or {}
            why = not_for_hiring(r.get("to", ""), src.get("context", ""))
            job = jobs.get(row["job_id"])
            if not why or not job:
                continue
            facts = extract(job)
            score = self.d.scorer.get(row["job_id"])
            card = {"kind": "card", "reason": why, "subject_format": r.get("subject_format", ""), "draft_notes": r.get("draft_notes") or [],
                    "company_domain": r.get("company_domain", "")}
            card["card"] = self.card(facts, score, why)
            self.store("job_routes", "job_id", row["job_id"], card)
            with self.d.db.tx() as c:
                c.execute("UPDATE drafts SET state='withdrawn' WHERE job_id=? AND channel='email' AND state='waiting'", (row["job_id"],))
                c.execute("UPDATE outbox SET state='refused', reason=?, approved_ts=NULL WHERE job_id=? AND state IN ('waiting','held','undo','queued')",
                          (why[:300], row["job_id"]))
            changed.append(row["job_id"])
        if changed:
            self.d.db.event("route", f"{len(changed)} roles moved from an email to a form card (the address is not for hiring)", self.d.clock)
        return changed

    def mark_applied(self, job_id: int) -> bool:
        job = next((j for j in self.d.job_fetch.store.all() if j["id"] == job_id), None)
        if not job:
            return False
        from .postfacts import extract
        self.d.scorer.mark_applied(extract(job), "form")
        self.d.db.event("applied", f"Marked as applied: job {job_id}", self.d.clock)
        return True

    def kinds(self) -> dict[int, str]:
        """job id -> "email" or "card"."""
        return {x["job_id"]: x["kind"] for x in self.d.db.query("SELECT job_id, kind FROM job_routes")}

    def form_cards(self) -> list[dict]:
        """The "apply via form" cards that are not marked as applied: the card fields plus job_id and reason."""
        applied = {a["job_id"] for a in self.d.db.query("SELECT job_id FROM applied WHERE job_id IS NOT NULL")}
        out = []
        for row in self.d.db.query("SELECT job_id, route FROM job_routes WHERE kind='card' ORDER BY ts, job_id"):
            route = json.loads(row["route"])
            if row["job_id"] not in applied and route.get("card"):
                out.append({"job_id": row["job_id"], "reason": route.get("reason", ""), **route["card"]})
        return out

    def get(self, job_id: int) -> dict | None:
        rows = self.d.db.query("SELECT route FROM job_routes WHERE job_id=?", (job_id,))
        return json.loads(rows[0]["route"]) if rows else None

    def store(self, table: str, key: str, value: int, r: dict) -> None:
        with self.d.db.tx() as c:
            c.execute(f"INSERT INTO {table}({key}, kind, route, ts) VALUES(?, ?, ?, ?) ON CONFLICT({key}) DO UPDATE SET "
                      "kind=excluded.kind, route=excluded.route, ts=excluded.ts", (value, r["kind"], json.dumps(r), self.d.clock()))

    # ---- the speculative kind: a dataset company with no matching role ----
    def speculative(self, company: dict, live_jobs=None) -> dict:
        """Only to careers@ and the like on the company's own careers or contact page. No address means no speculative mail."""
        domain = registrable(company.get("domain") or "")
        name = company.get("name", "")
        def none(reason: str) -> dict:
            r = {"kind": "none", "reason": reason, "company": name}
            if company.get("id") is not None:
                self.store("speculative", "company_id", company["id"], r)
            return r
        if not domain:
            return none("The company has no website in your list.")
        if self.d.blocklist.is_blocked(domain):
            return none("The company is on your blocklist.")
        cn = norm_company(name)
        jobs = self.d.job_fetch.store.live() if live_jobs is None else live_jobs
        if any(norm_company(j["company"]) == cn for j in jobs):
            return none("The company has an open role. Friday applies for the role instead.")
        cutoff = self.d.clock() - COOL_OFF_DAYS * 86400
        if self.d.db.query("SELECT 1 FROM applied WHERE company_norm=? AND ts>=?", (cn, cutoff)):
            return none(f"You wrote to this company in the last {COOL_OFF_DAYS} days.")
        careers = company.get("careers_url") or ""
        starts = [careers] if registrable(host_of(careers)) == domain else []
        pages = self.read_pages(domain, starts + [f"https://{domain}/"])
        if any(p.get("form_only") or p.get("no_unsolicited") for p in pages):
            return none("The company's page asks for applications via the form only.")
        notes = {"no_recruiters": any(p.get("no_recruiters") for p in pages), "no_agencies": any(p.get("no_agencies") for p in pages)}
        r = plan({"notes": notes, "emails": [], "source": "dataset"}, domain, pages)
        if r["kind"] != "email":
            return none("No careers address on the company's own site. Friday does not guess one.")
        r = {**r, "kind": "speculative", "company": name}
        if company.get("id") is not None:
            self.store("speculative", "company_id", company["id"], r)
        return r
