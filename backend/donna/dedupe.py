"""One copy of each job (JOB-SOURCES 4.2), and expiry (4.1).

The same role often shows up on the company board and on 2 or 3 remote boards. Two copies are the same job when:
1. their normalised apply (or job) addresses are equal; or
2. the company names match after normalising, the seniority is the same, the titles are at least 90% similar, and the
   locations do not disagree. Two copies from the same source are never merged this way (a board lists distinct roles).
The job keeps the best copy's fields: the company's own board first, then HN, then the feeds. Every copy stays as a row, so
the list of all sources that showed the job is kept.

Expiry: each copy belongs to a scope (one board, one feed address, the HN thread). After a good read of a scope, every live
copy of that scope that was not in the answer gets one miss for that day. Two misses in a row expire the copy. A job expires
when none of its copies is live. A copy seen again comes back to life.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .sources import RANK

MISSES_TO_EXPIRE = 2
TITLE_SIMILAR = 0.90

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, company TEXT NOT NULL, company_norm TEXT NOT NULL, title TEXT NOT NULL,
    title_norm TEXT NOT NULL, seniority TEXT NOT NULL, location TEXT NOT NULL, remote INTEGER NOT NULL, posted TEXT NOT NULL,
    job_url TEXT NOT NULL, apply_url TEXT NOT NULL, description TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
    extra TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL, rank INTEGER NOT NULL, primary_copy INTEGER,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, state TEXT NOT NULL, expired_day TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company_norm);
CREATE TABLE IF NOT EXISTS job_copies (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, source TEXT NOT NULL, ext_id TEXT NOT NULL,
    scope TEXT NOT NULL, job_url TEXT NOT NULL, apply_url TEXT NOT NULL, apply_key TEXT NOT NULL, url_key TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, missed INTEGER NOT NULL DEFAULT 0, missed_day TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL, UNIQUE(source, ext_id));
CREATE INDEX IF NOT EXISTS copies_apply ON job_copies(apply_key);
CREATE INDEX IF NOT EXISTS copies_url ON job_copies(url_key);
CREATE INDEX IF NOT EXISTS copies_scope ON job_copies(scope, state);
"""

def days_between(a: str, b: str) -> int | None:
    """Whole days from ISO day `a` to `b`; None when either is not a date. The one day-difference helper (DJ11 L4)."""
    from datetime import date
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except ValueError:
        return None


# ---- normalising ----

_TRACKING = {"ref", "source", "src", "gh_src", "lever-source", "lever-origin", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
             "trk", "referrer", "referer", "campaign", "medium", "via", "s", "_hsenc", "_hsmi", "igshid"}
_HOST_ALIAS = {"boards.greenhouse.io": "job-boards.greenhouse.io", "www.remoteok.com": "remoteok.com",
               "www.weworkremotely.com": "weworkremotely.com", "www.arbeitnow.com": "arbeitnow.com"}
_APPLY_TAIL = re.compile(r"/(apply|application)$")


def normalise_url(url: str) -> str:
    """Lower-case scheme and host, no www alias, no tracking parameters, no fragment, no trailing slash, no /apply tail."""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlsplit(u)
        port = p.port
    except ValueError:
        return ""
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return ""
    host = p.hostname.lower()
    host = _HOST_ALIAS.get(host, host)
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in _TRACKING)
    path = _APPLY_TAIL.sub("", re.sub(r"/+$", "", p.path or ""))
    return urlunsplit(("https", host, path, urlencode(query), ""))


_COMPANY_DROP = {"inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag", "sa", "bv", "co", "corp", "corporation",
                 "company", "labs", "lab", "ai", "com", "io", "pvt", "private", "technologies", "the"}


def norm_company(name: str) -> str:
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", (name or "").lower())
    s = s.replace(".com", " com").replace(".io", " io").replace(".ai", " ai")
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w not in _COMPANY_DROP]
    return " ".join(words)


_SENIORITY = (("senior", r"senior|sr"), ("staff", r"staff"), ("principal", r"principal"), ("lead", r"lead"),
              ("junior", r"junior|jr"), ("intern", r"intern|internship"))
_TITLE_DROP = re.compile(r"\b(remote|fully remote|100% remote|hybrid|on-?site|anywhere|worldwide)\b", re.I)


def norm_title(title: str) -> tuple[str, str]:
    """(title without seniority, brackets or "remote", seniority word or "")."""
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", (title or "").lower())
    s = _TITLE_DROP.sub(" ", s)
    seniority = ""
    for word, pattern in _SENIORITY:
        if re.search(rf"\b({pattern})\b\.?", s):
            seniority = seniority or word
            s = re.sub(rf"\b({pattern})\b\.?", " ", s)
    s = re.sub(r"[^a-z0-9+#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(), seniority


_PLACE_ALIAS = ((r"united states( of america)?|\busa\b|\bu\.s\.a?\.?", " us "), (r"united kingdom|great britain", " uk "),
                (r"\bbangalore\b", " bengaluru "), (r"\bbombay\b", " mumbai "), (r"\bgurgaon\b", " gurugram "),
                (r"\bnew york city\b|\bnyc\b", " new york "))
_PLACE_DROP = {"remote", "anywhere", "worldwide", "global", "hybrid", "onsite", "on", "site", "office", "only", "or", "and", "the",
               "of", "in", "based", "time", "zone", "timezone", "timezones", "friendly", "work", "from", "home", "wfh", "distributed",
               "first", "fully", "full", "within", "any", "location", "locations", "flexible"}


def norm_location(loc: str) -> set[str]:
    s = " " + (loc or "").lower() + " "
    for pattern, repl in _PLACE_ALIAS:
        s = re.sub(pattern, repl, s)
    return {w for w in re.split(r"[^a-z0-9]+", s) if w and w not in _PLACE_DROP and not w.isdigit()}


def same_title(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILAR)


def same_location(a: str, b: str) -> bool:
    """True unless both name places and the places do not overlap ("Remote" and "Worldwide" never disagree)."""
    ta, tb = norm_location(a), norm_location(b)
    return not ta or not tb or bool(ta & tb)


def rank(source: str) -> int:
    return RANK.get(source, 3)


# ---- the store ----

_MAIN = ("company", "title", "location", "remote", "posted", "job_url", "apply_url", "description", "hidden")


class JobStore:
    def __init__(self, db):
        self.db = db

    def _match(self, c, copy: dict, apply_key: str, url_key: str) -> int | None:
        keys = [k for k in {apply_key, url_key} if k]
        if keys:
            marks = ",".join("?" * len(keys))
            r = c.execute(f"SELECT job_id FROM job_copies WHERE apply_key IN ({marks}) OR url_key IN ({marks}) ORDER BY id LIMIT 1",
                          (*keys, *keys)).fetchone()
            if r:
                return r["job_id"]
        cn = norm_company(copy["company"])
        if not cn:
            return None
        tn, sen = norm_title(copy["title"])
        for j in c.execute("SELECT id, title_norm, location FROM jobs WHERE company_norm=? AND seniority=? ORDER BY id", (cn, sen)):
            if c.execute("SELECT 1 FROM job_copies WHERE job_id=? AND source=?", (j["id"], copy["source"])).fetchone():
                continue
            if same_title(tn, j["title_norm"]) and same_location(copy["location"], j["location"]):
                return j["id"]
        return None

    def _set_main(self, c, job_id: int, copy: dict, copy_id: int) -> None:
        tn, sen = norm_title(copy["title"])
        c.execute("UPDATE jobs SET company=?, company_norm=?, title=?, title_norm=?, seniority=?, location=?, remote=?, posted=?, "
                  "job_url=?, apply_url=?, description=?, hidden=?, extra=?, source=?, rank=?, primary_copy=? WHERE id=?",
                  (copy["company"], norm_company(copy["company"]), copy["title"], tn, sen, copy["location"], copy["remote"],
                   copy["posted"], copy["job_url"], copy["apply_url"], copy["description"], copy["hidden"],
                   json.dumps(copy.get("extra") or {}, ensure_ascii=False), copy["source"], rank(copy["source"]), copy_id, job_id))

    def add(self, copy: dict, scope: str, today: str) -> tuple[int, int, str]:
        """(job id, copy id, what happened: new, seen or merged). The copy must be in the shape `sources.common.job` gives."""
        return self.add_many([copy], scope, today)[0]

    def add_many(self, copies: list[dict], scope: str, today: str) -> list[tuple[int, int, str]]:
        """Many copies from one read, in one transaction (a big board has hundreds of jobs)."""
        with self.db.tx() as c:
            return [self._add(c, copy, scope, today) for copy in copies]

    def _add(self, c, copy: dict, scope: str, today: str) -> tuple[int, int, str]:
        copy = {**{k: "" for k in _MAIN}, **copy}
        copy["remote"], copy["hidden"] = int(copy["remote"] or 0), int(copy["hidden"] or 0)
        apply_key, url_key = normalise_url(copy["apply_url"]), normalise_url(copy["job_url"])
        old = c.execute("SELECT id, job_id FROM job_copies WHERE source=? AND ext_id=?", (copy["source"], copy["ext_id"])).fetchone()
        if old:
            copy_id, job_id, what = old["id"], old["job_id"], "seen"
            c.execute("UPDATE job_copies SET scope=?, job_url=?, apply_url=?, apply_key=?, url_key=?, last_seen=?, missed=0, "
                      "missed_day='', state='live' WHERE id=?", (scope, copy["job_url"], copy["apply_url"], apply_key, url_key, today, copy_id))
        else:
            job_id = self._match(c, copy, apply_key, url_key)
            what = "merged" if job_id else "new"
            if not job_id:
                job_id = c.execute("INSERT INTO jobs(company, company_norm, title, title_norm, seniority, location, remote, posted, "
                                   "job_url, apply_url, description, source, rank, first_seen, last_seen, state) "
                                   "VALUES('', '', '', '', '', '', 0, '', '', '', '', '', 99, ?, ?, 'live')", (today, today)).lastrowid
            copy_id = c.execute("INSERT INTO job_copies(job_id, source, ext_id, scope, job_url, apply_url, apply_key, url_key, "
                                "first_seen, last_seen, state) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')",
                                (job_id, copy["source"], copy["ext_id"], scope, copy["job_url"], copy["apply_url"], apply_key,
                                 url_key, today, today)).lastrowid
        j = c.execute("SELECT rank, primary_copy FROM jobs WHERE id=?", (job_id,)).fetchone()
        if j["primary_copy"] == copy_id or rank(copy["source"]) < j["rank"]:
            self._set_main(c, job_id, copy, copy_id)        # a better copy wins; the kept copy refreshes its own fields
        c.execute("UPDATE jobs SET last_seen=?, state='live', expired_day='' WHERE id=?", (today, job_id))
        return job_id, copy_id, what

    def _refresh_jobs(self, c, job_ids, today: str) -> int:
        """Expire jobs with no live copy. Returns how many expired now."""
        n = 0
        for jid in set(job_ids):
            if not c.execute("SELECT 1 FROM job_copies WHERE job_id=? AND state='live'", (jid,)).fetchone():
                n += c.execute("UPDATE jobs SET state='expired', expired_day=? WHERE id=? AND state='live'", (today, jid)).rowcount
        return n

    def missing(self, scope: str, seen_copy_ids, today: str) -> dict:
        """After a good read of `scope`: one miss (once a day) for each live copy not seen; expire at two misses in a row."""
        seen = set(seen_copy_ids)
        missed = expired_copies = 0
        with self.db.tx() as c:
            rows = c.execute("SELECT id, job_id, missed, missed_day FROM job_copies WHERE scope=? AND state='live'", (scope,)).fetchall()
            touched = []
            for r in rows:
                if r["id"] in seen or r["missed_day"] == today:
                    continue
                n = r["missed"] + 1
                state = "expired" if n >= MISSES_TO_EXPIRE else "live"
                c.execute("UPDATE job_copies SET missed=?, missed_day=?, state=? WHERE id=?", (n, today, state, r["id"]))
                missed += 1
                if state == "expired":
                    expired_copies += 1
                    touched.append(r["job_id"])
            expired = self._refresh_jobs(c, touched, today)
        return {"missed": missed, "expired": expired, "expired_copies": expired_copies}

    def unchanged(self, scope: str, today: str) -> int:
        """A 304 answer: every live copy of the scope is still there."""
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET last_seen=? WHERE id IN (SELECT job_id FROM job_copies WHERE scope=? AND state='live')", (today, scope))
            return c.execute("UPDATE job_copies SET last_seen=?, missed=0, missed_day='' WHERE scope=? AND state='live'", (today, scope)).rowcount

    def expire_scope(self, scope: str, today: str) -> int:
        """The board is gone (moved ATS): its copies expire at once."""
        with self.db.tx() as c:
            ids = [r["job_id"] for r in c.execute("SELECT job_id FROM job_copies WHERE scope=? AND state='live'", (scope,))]
            c.execute("UPDATE job_copies SET state='expired', missed_day=? WHERE scope=? AND state='live'", (today, scope))
            return self._refresh_jobs(c, ids, today)

    def jobs(self, state: str | None = "live") -> list[dict]:
        return self._rows(state)

    def _rows(self, state: str | None) -> list[dict]:
        where, params = ("WHERE j.state=?", (state,)) if state else ("", ())
        rows = self.db.query(f"SELECT j.* FROM jobs j {where} ORDER BY j.posted DESC, j.id DESC", params)
        copies = self.db.query("SELECT job_id, source, scope, job_url, state, first_seen, last_seen FROM job_copies ORDER BY id")
        by: dict[int, list] = {}
        for cp in copies:
            by.setdefault(cp["job_id"], []).append(cp)
        for r in rows:
            r["extra"] = json.loads(r["extra"] or "{}")
            r["copies"] = by.get(r["id"], [])
            r["sources"] = sorted({cp["source"] for cp in r["copies"]}, key=lambda s: (rank(s), s))
        return rows

    def get(self, job_id: int) -> dict | None:
        return next((r for r in self._rows(None) if r["id"] == job_id), None)

    def live(self) -> list[dict]:
        return self._rows("live")

    def all(self) -> list[dict]:
        return self._rows(None)

    def counts(self) -> dict:
        rows = self.db.query("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        by = {"live": 0, "expired": 0}
        by.update({r["state"]: r["n"] for r in rows})
        return by
