"""The owner's companies dataset and the seed board list (DJ03).

Dataset: a CSV upload, or a published Google Sheet CSV link (fetched by the DJ01 worker, never in this process). Columns are mapped
by their header: company, website, careers page, country, notes. Every other column is kept as an insight. Websites become domains.
A careers link on a known job board gives the board: Greenhouse, Lever, Ashby, Workable or Recruitee. XLSX is phase 2.

Seed list: `seed-companies.csv`, all rows imported as boards. A board is active when `india_ok` is yes or check;
the rest are kept on watch (PLAN-FINAL row 35). The notes stay: some names carry a warning.

A domain on the owner's blocklist is never imported.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import blocklist

ROOT = Path(__file__).resolve().parents[2]
SEED_CSV = ROOT / "seed-companies.csv"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000
MAX_CELL = 500
MAX_INSIGHTS = 30
ATS = ("greenhouse", "lever", "ashby", "workable", "recruitee")
STATES = ("active", "watch", "moved", "off")      # moved: set by the fetch (DJ04) when a board answers 404
ACTIVE_INDIA = ("yes", "check")

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (id INTEGER PRIMARY KEY, ats TEXT NOT NULL, token TEXT NOT NULL, company TEXT NOT NULL,
    careers_url TEXT NOT NULL, remote_ok TEXT NOT NULL DEFAULT '', india_ok TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL, source TEXT NOT NULL, added_ts REAL NOT NULL, UNIQUE(ats, token));
-- key: the domain, or 'name:<lower-case name>' when the row has no website.
CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, domain TEXT NOT NULL,
    careers_url TEXT NOT NULL, country TEXT NOT NULL, notes TEXT NOT NULL, insights TEXT NOT NULL, board_id INTEGER,
    source TEXT NOT NULL, added_ts REAL NOT NULL);
"""

# Header words for each field (compared after lower case and punctuation removed).
COLUMNS = {
    "company": ("company", "company name", "name", "organisation", "organization", "org", "employer", "business"),
    "website": ("website", "website url", "site", "url", "domain", "homepage", "home page", "web", "company website"),
    "careers": ("careers page", "careers", "careers url", "career page", "careers link", "jobs page", "jobs url", "jobs",
                "job board", "board url", "ats url", "hiring page"),
    "country": ("country", "hq country", "hq", "headquarters", "location", "country hq"),
    "notes": ("notes", "note", "comments", "comment", "remarks"),
}
_FIELD_OF = {h: f for f, hs in COLUMNS.items() for h in hs}

_TOKEN = r"([A-Za-z0-9][A-Za-z0-9_.-]{0,99})"
BOARD_PATTERNS = (
    ("greenhouse", re.compile(rf"^(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io$"), re.compile(rf"^/{_TOKEN}(?:/|$)")),
    ("lever", re.compile(r"^jobs(?:\.eu)?\.lever\.co$"), re.compile(rf"^/{_TOKEN}(?:/|$)")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$"), re.compile(rf"^/{_TOKEN}(?:/|$)")),
    ("smartrecruiters", re.compile(r"^(?:careers|jobs)\.smartrecruiters\.com$"), re.compile(rf"^/{_TOKEN}(?:/|$)")),
    ("workable", re.compile(r"^apply\.workable\.com$"), re.compile(rf"^/{_TOKEN}(?:/|$)")),
)
_RECRUITEE = re.compile(rf"^{_TOKEN}\.recruitee\.com$")
_NOT_TOKENS = {"embed", "api", "v1", "j", "jobs", "careers", "job_board", "static"}
BOARD_URL = {"greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
             "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://careers.smartrecruiters.com/{}", "workable": "https://apply.workable.com/{}", "recruitee": "https://{}.recruitee.com"}
_BOARD_HOST = re.compile(r"(^|\.)(greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|recruitee\.com|smartrecruiters\.com)$")


class DatasetError(Exception):
    """A plain sentence for the page."""


def domain_of(url: str) -> str:
    return blocklist.normalise(url)


def detect_board(url: str) -> tuple[str, str] | None:
    """(ats, token) for a careers link on a known job board, else None."""
    u = (url or "").strip()
    if not u:
        return None
    if "://" not in u:
        u = "https://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    m = _RECRUITEE.match(host)
    if m and m.group(1).lower() not in ("www", "app", "api", "careers"):
        return "recruitee", m.group(1).lower()
    for ats, host_re, path_re in BOARD_PATTERNS:
        if host_re.match(host):
            pm = path_re.match(parts.path or "/")
            if pm and pm.group(1).lower() not in _NOT_TOKENS:
                return ats, pm.group(1).lower()
            if ats == "greenhouse":                       # the embed form: boards.greenhouse.io/embed/job_board?for=token
                tok = parse_qs(parts.query).get("for", [""])[0]
                if re.fullmatch(_TOKEN, tok):
                    return ats, tok.lower()
    return None


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (h or "").lower())).strip()


def map_columns(header: list[str]) -> dict[str, int]:
    """field -> column index. The first matching column wins."""
    out: dict[str, int] = {}
    for i, h in enumerate(header):
        f = _FIELD_OF.get(_norm_header(h))
        if f and f not in out:
            out[f] = i
    return out


def _decode(data) -> str:
    if isinstance(data, str):
        return data
    if len(data) > MAX_BYTES:
        raise DatasetError("The file is larger than 5 MB.")
    if data[:4] == b"PK\x03\x04":
        raise DatasetError("This looks like an Excel file. Save it as CSV first (Excel files come in a later version).")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def read_csv(data) -> tuple[list[str], list[list[str]]]:
    text = _decode(data)
    if len(text) > MAX_BYTES:
        raise DatasetError("The file is larger than 5 MB.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    try:
        for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
            if i > MAX_ROWS:
                raise DatasetError(f"The file has more than {MAX_ROWS} rows. Split it into smaller files.")
            rows.append([c.strip()[:MAX_CELL] for c in row])
    except csv.Error:
        raise DatasetError("The file is not a readable CSV.")
    rows = [r for r in rows if any(r)]
    if not rows:
        raise DatasetError("The file is empty.")
    return rows[0], rows[1:]


def _careers_url(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if "://" not in v:
        v = "https://" + v
    try:
        p = urlsplit(v)
    except ValueError:
        return ""
    return v[:300] if p.scheme in ("http", "https") and domain_of(v) else ""


class Dataset:
    def __init__(self, donna):
        self.d = donna

    # ---- boards ----
    def _add_board(self, c, ats: str, token: str, company: str, careers_url: str, state: str, source: str,
                   remote_ok: str = "", india_ok: str = "", notes: str = "") -> tuple[int, bool]:
        """(board id, added). An existing board keeps its state: the owner may have switched it."""
        r = c.execute("SELECT id FROM boards WHERE ats=? AND token=?", (ats, token)).fetchone()
        if r:
            if source == "seed":
                c.execute("UPDATE boards SET company=?, careers_url=?, remote_ok=?, india_ok=?, notes=? WHERE id=?",
                          (company, careers_url, remote_ok, india_ok, notes, r["id"]))
            return r["id"], False
        cur = c.execute("INSERT INTO boards(ats, token, company, careers_url, remote_ok, india_ok, notes, state, source, added_ts) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (ats, token, company, careers_url, remote_ok, india_ok, notes, state, source, self.d.clock()))
        return cur.lastrowid, True

    def import_seed(self, path: Path | None = None) -> dict:
        """Every row of the seed list as a board. Active when india_ok is yes or check; else on watch."""
        path = Path(path or SEED_CSV)
        try:
            header, rows = read_csv(path.read_bytes())
        except OSError:
            return {"ok": False, "error": "The seed list file is missing."}
        except DatasetError as e:
            return {"ok": False, "error": str(e)}
        cols = {_norm_header(h).replace(" ", "_"): i for i, h in enumerate(header)}
        need = ("company", "ats", "board_token", "careers_url", "remote_ok", "india_ok", "notes")
        if any(k not in cols for k in need):
            return {"ok": False, "error": "The seed list needs the columns " + ", ".join(need) + "."}
        rules = self.d.blocklist.rules()
        added = updated = skipped = 0
        with self.d.db.tx() as c:
            for row in rows:
                g = {k: (row[cols[k]] if cols[k] < len(row) else "") for k in need}
                ats, token = g["ats"].lower(), g["board_token"].lower()
                if ats not in ATS or not re.fullmatch(_TOKEN, token) or not g["company"] \
                        or self.d.blocklist.company_blocked(g["company"], [g["careers_url"], f"https://{ats}.invalid/{token}"], rules=rules):
                    skipped += 1
                    continue
                india = g["india_ok"].lower()
                state = "active" if india in ACTIVE_INDIA else "watch"
                url = _careers_url(g["careers_url"]) or BOARD_URL[ats].format(token)
                _id, new = self._add_board(c, ats, token, g["company"], url, state, "seed", g["remote_ok"].lower(), india, g["notes"])
                added += new
                updated += not new
        self.d.db.event("dataset", f"Seed list: {added} boards added, {updated} updated, {skipped} skipped", self.d.clock)
        return {"ok": True, "rows": len(rows), "added": added, "updated": updated, "skipped": skipped, **self.counts()}

    def boards(self, state: str | None = None) -> list[dict]:
        if state:
            return self.d.db.query("SELECT * FROM boards WHERE state=? ORDER BY company COLLATE NOCASE", (state,))
        return self.d.db.query("SELECT * FROM boards ORDER BY company COLLATE NOCASE")

    def set_board_state(self, board_id: int, state: str) -> bool:
        """The owner's switch: active, watch or off. `moved` belongs to the fetch (DJ04)."""
        if state not in ("active", "watch", "off"):
            return False
        with self.d.db.tx() as c:
            return c.execute("UPDATE boards SET state=? WHERE id=?", (state, int(board_id))).rowcount == 1

    def counts(self) -> dict:
        rows = self.d.db.query("SELECT state, COUNT(*) AS n FROM boards GROUP BY state")
        by = {s: 0 for s in STATES}
        by.update({r["state"]: r["n"] for r in rows})
        return {"boards": sum(by.values()), "by_state": by,
                "companies": self.d.db.query("SELECT COUNT(*) AS n FROM companies")[0]["n"]}

    # ---- the owner's dataset ----
    def upsert_company(self, c, rules, name: str, website: str, careers: str, country: str, notes: str, insights: dict,
                       source: str) -> tuple[str, int, int, int | None]:
        """One company row, inside the caller's transaction. Returns (added | updated | blocked | empty, board found, board added,
        company id). The blocklist always wins. A website that is a job board link becomes the careers link."""
        domain = domain_of(website)
        if domain and _BOARD_HOST.search(domain):          # a board link in the website column
            careers, domain = careers or _careers_url(website), ""
        name = (name or "").strip() or domain
        if not name:
            return "empty", 0, 0, None
        if self.d.blocklist.company_blocked(name, [v for v in (domain, careers, website) if v], rules=rules):
            return "blocked", 0, 0, None
        board = detect_board(careers) or detect_board(website)
        board_id, new = None, 0
        if board:
            board_id, made = self._add_board(c, board[0], board[1], name, BOARD_URL[board[0]].format(board[1]), "active", "dataset")
            new = int(made)
        key = domain or "name:" + name.lower()
        values = (name, domain, careers, country, notes, json.dumps(insights, ensure_ascii=False), board_id)
        r = c.execute("SELECT id, board_id FROM companies WHERE key=?", (key,)).fetchone()
        if r:
            c.execute("UPDATE companies SET name=?, domain=?, careers_url=?, country=?, notes=?, insights=?, board_id=? WHERE id=?",
                      values[:-1] + (board_id or r["board_id"], r["id"]))
            return "updated", int(bool(board)), new, r["id"]
        cur = c.execute("INSERT INTO companies(key, name, domain, careers_url, country, notes, insights, board_id, source, added_ts) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (key, *values, source, self.d.clock()))
        return "added", int(bool(board)), new, cur.lastrowid

    def import_csv(self, data, source: str = "upload") -> dict:
        """Map the columns, keep the rest as insights, normalise websites, find boards. A wrong file changes nothing."""
        try:
            header, rows = read_csv(data)
        except DatasetError as e:
            return {"ok": False, "error": str(e)}
        cols = map_columns(header)
        if "company" not in cols and "website" not in cols:
            return {"ok": False, "error": "The file needs a column named company (or website). The first row must hold the column names."}
        used = set(cols.values())
        extra = [(i, h.strip() or f"Column {i + 1}") for i, h in enumerate(header) if i not in used][:MAX_INSIGHTS]
        rules = self.d.blocklist.rules()
        stats = {"rows": len(rows), "added": 0, "updated": 0, "boards_found": 0, "boards_added": 0, "skipped_blocked": 0,
                 "skipped_empty": 0}
        source = re.sub(r"[^\w .:-]", "", source)[:60] or "upload"
        with self.d.db.tx() as c:
            for row in rows:
                cell = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else ""   # noqa: E731
                insights = {h: row[i] for i, h in extra if i < len(row) and row[i]}
                what, board_found, board_new, _cid = self.upsert_company(c, rules, cell("company"), cell("website"), _careers_url(cell("careers")),
                                                                         cell("country"), cell("notes"), insights, source)
                if what == "empty":
                    stats["skipped_empty"] += 1
                elif what == "blocked":
                    stats["skipped_blocked"] += 1
                else:
                    stats[what] += 1
                    stats["boards_found"] += board_found
                    stats["boards_added"] += board_new
        mapping = {f: header[i] for f, i in cols.items()}
        self.d.db.event("dataset", f"Dataset ({source}): {stats['added']} added, {stats['updated']} updated, "
                                   f"{stats['boards_found']} boards found, {stats['skipped_blocked']} blocked", self.d.clock)
        return {"ok": True, **stats, "mapping": mapping, "insight_columns": [h for _i, h in extra], **self.counts()}

    @staticmethod
    def sheet_link_ok(url: str) -> bool:
        """A published Google Sheet CSV link: https, docs.google.com, /spreadsheets/d/..., output=csv or format=csv."""
        try:
            p = urlsplit((url or "").strip())
        except ValueError:
            return False
        q = parse_qs(p.query)
        return p.scheme == "https" and (p.hostname or "") == "docs.google.com" and p.port is None and not p.username \
            and p.path.startswith("/spreadsheets/d/") and ("csv" in q.get("output", []) or "csv" in q.get("format", []))

    def import_sheet(self, url: str) -> dict:
        """Fetch the published sheet through the worker (no secret, network rules), then import it as CSV."""
        if not self.sheet_link_ok(url):
            return {"ok": False, "error": "Use the CSV link of a published Google Sheet (File, Share, Publish to web, CSV)."}
        r = self.d.fetch(url.strip())
        if not r.get("ok"):
            return {"ok": False, "error": "The sheet could not be fetched: " + str(r.get("error") or "unknown problem")[:200]}
        if r.get("status") != 200:
            return {"ok": False, "error": f"The sheet could not be fetched (the site answered {r.get('status')}). Check it is published."}
        if r.get("truncated"):
            return {"ok": False, "error": "The sheet is too large. Download it as CSV and upload the file."}
        body = str(r.get("body") or "")
        if body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            return {"ok": False, "error": "The link gave a web page, not CSV. Publish the sheet as CSV and use that link."}
        return self.import_csv(body, source="google sheet")

    def companies(self) -> list[dict]:
        rows = self.d.db.query("SELECT c.*, b.ats, b.token, b.state AS board_state FROM companies c "
                               "LEFT JOIN boards b ON b.id = c.board_id ORDER BY c.name COLLATE NOCASE")
        for r in rows:
            r["insights"] = json.loads(r["insights"] or "{}")
        return rows

    def page_data(self) -> dict:
        return {**self.counts(), "boards_list": self.boards(), "companies_list": self.companies()}
