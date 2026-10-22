"""The owner's CV and the facts Friday may claim (DJ03).

The CV is a PDF only: checked by its first bytes (`%PDF-`), at most 5 MB, stored as `data/Friday/cv/cv.pdf` (owner-only file).
Code (no model) reads the text and builds a facts list: skills, years, roles, employers, projects, education. Every fact starts
unticked. Only ticked facts count: the writer (DJ06) gets `claimable()` and nothing else from the CV. The CV text itself is not stored.

A new CV keeps the ticks of facts that are still on it, and keeps the facts the owner typed in. When the PDF has no readable text
(a scanned image), the owner can paste the text instead; the PDF stays the attachment.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import date
from pathlib import Path

from . import pdftext

MAX_BYTES = 5 * 1024 * 1024
MAGIC = b"%PDF-"
MAX_TEXT = 100_000
KINDS = ("skill", "years", "role", "employer", "project", "education")
LIMITS = {"skill": 60, "years": 6, "role": 15, "employer": 15, "project": 15, "education": 8}
MAX_FACT = 160

SCHEMA = """
CREATE TABLE IF NOT EXISTS cv_meta (id INTEGER PRIMARY KEY CHECK (id = 1), filename TEXT NOT NULL, size INTEGER NOT NULL,
    sha256 TEXT NOT NULL, chars INTEGER NOT NULL, uploaded_ts REAL NOT NULL);
-- source: 'cv' (read from the CV by code) or 'owner' (typed on the page). Only ticked facts may be claimed.
CREATE TABLE IF NOT EXISTS cv_facts (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, ticked INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL, pos INTEGER NOT NULL, UNIQUE(kind, text));
"""


class CVError(Exception):
    """A plain sentence for the page."""


def check_pdf(data: bytes) -> None:
    if not data:
        raise CVError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise CVError("The CV is larger than 5 MB. Save a smaller PDF and try again.")
    if not data.startswith(MAGIC):
        raise CVError("Only a PDF file can be used as your CV.")


# ---- reading facts from the text (code only) ----

_HEADINGS = {
    "skills": ("skills", "technical skills", "core skills", "key skills", "tech stack", "technologies", "technical expertise",
               "tools", "tools and technologies", "skills and tools", "core competencies", "competencies", "expertise", "stack"),
    "experience": ("experience", "work experience", "professional experience", "employment", "employment history", "work history",
                   "career history", "relevant experience", "experience summary"),
    "projects": ("projects", "personal projects", "key projects", "side projects", "selected projects", "open source",
                 "academic projects", "notable projects"),
    "education": ("education", "academic", "academics", "qualifications", "education and training", "academic background",
                  "academic qualifications", "educational qualifications"),
    "other": ("summary", "profile", "about", "about me", "professional summary", "objective", "career objective", "certifications",
              "certificates", "awards", "achievements", "interests", "hobbies", "languages", "publications", "references", "contact",
              "personal details", "volunteering", "activities", "courses", "links", "honours", "honors", "leadership"),
}
_HEADING_OF = {h: sec for sec, hs in _HEADINGS.items() for h in hs}

# Well-known technical words, found anywhere in the CV. Spelling on the left is how the fact is shown.
KNOWN_SKILLS = (
    "Python", "JavaScript", "TypeScript", "Java", "Go", "Golang", "Rust", "C++", "C#", "Ruby", "PHP", "Kotlin", "Swift", "Scala",
    "SQL", "HTML", "CSS", "Sass", "Bash", "R", "Dart", "Elixir",
    "React", "React Native", "Next.js", "Vue", "Nuxt", "Angular", "Svelte", "Redux", "Tailwind", "jQuery", "Node.js", "Express",
    "NestJS", "Deno", "Django", "Flask", "FastAPI", "Spring", "Spring Boot", "Rails", "Laravel", ".NET", "GraphQL", "REST", "gRPC",
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch", "DynamoDB", "Cassandra", "Kafka", "RabbitMQ", "Celery",
    "Docker", "Kubernetes", "Terraform", "Ansible", "AWS", "GCP", "Azure", "Linux", "Git", "GitHub Actions", "CI/CD", "Jenkins",
    "Nginx", "Serverless", "Firebase", "Supabase", "Vercel", "Webpack", "Vite", "Jest", "Pytest", "Playwright", "Cypress",
    "Selenium", "Pandas", "NumPy", "PyTorch", "TensorFlow", "scikit-learn", "LangChain", "LLM", "LLMs", "RAG", "OpenAI",
    "Machine Learning", "Deep Learning", "NLP", "Computer Vision", "Spark", "Airflow", "dbt", "Snowflake", "BigQuery", "Tableau",
    "Power BI", "Figma", "Microservices", "WebSockets", "OAuth", "Stripe", "Shopify", "WordPress", "Electron", "Flutter",
    "Android", "iOS", "Unity", "Prometheus", "Grafana", "Datadog", "Sentry",
)
_SKILL_RE = {s: re.compile(r"(?<![\w.+#])" + re.escape(s) + r"(?![\w+#]|\.\w)", re.I if len(s) > 3 else 0)
             for s in KNOWN_SKILLS}
_AMBIGUOUS = {"Go", "R", "Rust", "Swift", "Spring", "Express", "Unity", "Spark", "REST"}   # need the skills section or a stack line

_ROLE_WORDS = ("engineer", "developer", "programmer", "manager", "lead", "architect", "analyst", "consultant", "intern",
               "designer", "scientist", "founder", "co-founder", "cto", "ceo", "head of", "director", "officer", "specialist",
               "administrator", "devops", "sre", "tester", "qa", "freelancer", "freelance", "contractor", "associate", "researcher",
               "trainee", "owner", "fellow", "mentor", "instructor", "teacher")
_ROLE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in _ROLE_WORDS) + r")s?\b", re.I)
_PLACES = {"remote", "india", "onsite", "on-site", "hybrid", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "gurugram",
           "gurgaon", "noida", "pune", "hyderabad", "chennai", "kolkata", "ahmedabad", "jaipur", "chandigarh", "kochi",
           "indore", "london", "berlin", "dubai", "singapore", "san francisco", "new york", "usa", "uk", "us", "full-time",
           "full time", "part-time", "part time", "contract", "internship"}
_DEGREE_RE = re.compile(r"\b(B\.?\s?Tech|M\.?\s?Tech|B\.?\s?E\.?|M\.?\s?E\.?|B\.?\s?Sc|M\.?\s?Sc|B\.?\s?C\.?\s?A|M\.?\s?C\.?\s?A|MBA|"
                        r"B\.?\s?Com|Ph\.?\s?D|Bachelor'?s?|Master'?s?|Diploma|Doctorate|Higher Secondary|Class XII|Class 12)\b", re.I)
_SCHOOL_RE = re.compile(r"\b(University|College|Institute|School|Academy|IIT|NIT|IIIT|BITS|Polytechnic|Vidyalaya)\b", re.I)

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s*'?\d{{2,4}}|\d{{1,2}}\s*/\s*(?:19|20)\d{{2}}|(?:19|20)\d{{2}})"
_END = rf"(?:{_DATE}|present|current|now|till date|to date|today|ongoing)"
_RANGE_RE = re.compile(rf"\(?\s*({_DATE})\s*(?:-|–|—|to|until|till)\s*({_END})\s*\)?", re.I)
_YEARS_RE = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\b(?:\s+of)?(?:\s+[\w/+#.-]+){0,6}", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣●○■□➢➤►>–—]|\d{1,2}[.)])\s*")
_SPLIT_RE = re.compile(r"\s+(?:at|@)\s+|\s*[|•·]\s*|\s+[–—-]\s+|\s*,\s+")


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", _BULLET_RE.sub("", s)).strip(" \t,;:|.-–—")
    return s[:MAX_FACT].strip()


def _heading(line: str) -> str | None:
    key = re.sub(r"[^a-z ]", "", line.lower().replace("&", "and")).strip()
    key = re.sub(r"\s+", " ", key)
    if not key or len(key.split()) > 4:
        return None
    return _HEADING_OF.get(key)


def _year(s: str, today: date) -> int | None:
    s = s.lower().strip()
    if re.match(r"present|current|now|till date|to date|today|ongoing", s):
        return today.year
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return int(m.group(0))
    m = re.search(r"'?(\d{2})$", s)
    return 2000 + int(m.group(1)) if m else None


def _is_place(part: str) -> bool:
    words = [w.strip(" ,()") for w in re.split(r",|/", part.lower())]
    return all(w in _PLACES for w in words if w)


def _employer_like(part: str) -> bool:
    words = part.split()
    return 0 < len(words) <= 6 and part[:1].isupper() and not _is_place(part) and not _ROLE_RE.search(part) \
        and not re.search(r"\d{3,}", part) and not part.endswith(".")


def sections(text: str) -> dict[str, list[str]]:
    """Lines by section. Lines before the first heading go to 'top'."""
    out: dict[str, list[str]] = {"top": []}
    cur = "top"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        sec = _heading(line.rstrip(":"))
        if sec:
            cur = sec
            out.setdefault(cur, [])
            continue
        if ":" in line and _heading(line.split(":", 1)[0]) == "skills" and cur != "skills":
            out.setdefault("skills", []).append(line.split(":", 1)[1])
            continue
        out.setdefault(cur, []).append(line)
    return out


def extract_facts(text: str, today: date | None = None) -> list[tuple[str, str]]:
    """(kind, text) pairs, in reading order, no duplicates. Code only; the result is a list of suggestions for the owner to tick."""
    today = today or date.today()
    text = text[:MAX_TEXT]
    sec = sections(text)
    found: dict[str, list[str]] = {k: [] for k in KINDS}
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str):
        value = _clean(value)
        if len(value) < 2 or len(found[kind]) >= LIMITS[kind]:
            return
        key = (kind, value.lower())
        if key not in seen:
            seen.add(key)
            found[kind].append(value)

    # skills: the skills section, item by item, then known words anywhere
    for line in sec.get("skills", []):
        line = line.split(":", 1)[1] if ":" in line and len(line.split(":", 1)[0].split()) <= 4 else line
        for item in re.split(r"\s*[,;|•·/]\s*|\s+-\s+|\t", _BULLET_RE.sub("", line)):
            item = re.sub(r"\(.*?\)", "", item).strip()
            if 1 <= len(item.split()) <= 4 and not item.endswith("."):
                add("skill", item)
    skills_text = " ".join(sec.get("skills", []))
    for name, rx in _SKILL_RE.items():
        if rx.search(text) and (name not in _AMBIGUOUS or rx.search(skills_text)):
            add("skill", name)

    # years: phrases such as "6+ years of experience in backend", then the span of the work dates
    for m in _YEARS_RE.finditer(text):
        n = float(m.group(1))
        if 0 < n <= 45 and re.search(r"experience|exp\b|work|build|develop|engineer|industry|professional|in\b", m.group(0), re.I):
            add("years", re.split(r"[.;\n]", m.group(0))[0])
    starts, ends = [], []
    for line in sec.get("experience", []):
        for m in _RANGE_RE.finditer(line):
            a, b = _year(m.group(1), today), _year(m.group(2), today)
            if a and b and 1970 <= a <= b <= today.year:
                starts.append(a)
                ends.append(b)
    if starts:
        span = max(ends) - min(starts)
        end = "today" if max(ends) == today.year else str(max(ends))
        if span >= 1:
            add("years", f"About {span} years of work ({min(starts)} to {end})")

    # roles and employers: header lines in the experience section
    exp = sec.get("experience", [])
    for i, line in enumerate(exp):
        if _BULLET_RE.match(line) or len(line.split()) > 14:
            continue
        has_range = bool(_RANGE_RE.search(line))
        body = _RANGE_RE.sub(" ", line).strip(" ,|()–—-")
        parts = [p.strip(" ()") for p in _SPLIT_RE.split(body) if p and p.strip(" ()")]
        roles = [p for p in parts if _ROLE_RE.search(p) and len(p.split()) <= 7]
        if not roles and not has_range and " at " not in f" {line} ":
            continue
        if not roles and has_range:                       # the role may sit on the line before or after
            for j in (i - 1, i + 1):
                if 0 <= j < len(exp) and not _BULLET_RE.match(exp[j]) and _ROLE_RE.search(exp[j]) and len(exp[j].split()) <= 7 \
                        and not _RANGE_RE.search(exp[j]):
                    roles = [_RANGE_RE.sub(" ", exp[j])]
                    break
        if not roles and not has_range:
            continue
        for r in roles:
            add("role", r)
        for p in parts:
            if p not in roles and _employer_like(p):
                add("employer", p)
                break

    # projects: the title of each project (a line that is not a bullet, or a "Name: what it does" bullet)
    for line in sec.get("projects", []):
        if _BULLET_RE.match(line):
            m = re.match(r"^([A-Z0-9][\w .+#&'-]{1,40}?)\s*(?::|\s[–—-]\s)\s*(.+)$", _BULLET_RE.sub("", line))
            if m:
                add("project", f"{m.group(1)}: {m.group(2)}")
            continue
        if line[:1].isupper() or line[:1].isdigit():
            if len(line.split()) <= 14 and not line.endswith(","):
                add("project", _RANGE_RE.sub(" ", line))

    # education: degree and school lines in the education section; else degree lines anywhere
    edu = sec.get("education", [])
    pool = edu if edu else [ln for ln in text.splitlines() if _DEGREE_RE.search(ln)]
    for line in pool:
        if (_DEGREE_RE.search(line) or _SCHOOL_RE.search(line)) and len(line.split()) <= 25:
            add("education", line)

    return [(k, v) for k in KINDS for v in found[k]]


# ---- the owner's CV and facts ----

class CV:
    def __init__(self, donna):
        self.d = donna

    @property
    def folder(self) -> Path:
        return Path(self.d.cfg.work_dir) / "cv"

    @property
    def path(self) -> Path:
        return self.folder / "cv.pdf"

    def _write_pdf(self, data: bytes) -> None:
        """Owner-only folder and file. A random temp name with O_EXCL | O_NOFOLLOW, then a rename."""
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        tmp = self.folder / f".cv-{secrets.token_hex(8)}.tmp"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def upload(self, data: bytes, filename: str = "") -> dict:
        """Check, read the text, store the PDF and the facts. A refused file changes nothing."""
        try:
            check_pdf(data)
            text = pdftext.extract_text(data)
        except CVError as e:
            return {"ok": False, "error": str(e)}
        except pdftext.PdfError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001: a broken file must not break the page; the type only
            return {"ok": False, "error": f"Friday could not read this PDF ({type(e).__name__}). Save it again as a PDF and retry."}
        self._write_pdf(data)
        name = re.sub(r"[^\w .()-]", "", Path(filename or "cv.pdf").name)[:80] or "cv.pdf"
        n = self._store(text, name, len(data), hashlib.sha256(data).hexdigest())
        out = {"ok": True, **self.page_data(), "found": n}
        if n == 0:
            out["warning"] = ("Friday found no text in this PDF. It may be a scanned image. Paste your CV text below "
                              "to build the facts list. The PDF is saved and stays your attachment.")
        return out

    def reread(self) -> dict:
        """Read the saved PDF again with the current reader (after a reader fix). Facts that were ticked and are still found stay ticked."""
        if not self.path.exists():
            return {"ok": False, "error": "Upload your CV as a PDF first."}
        data = self.path.read_bytes()
        try:
            text = pdftext.extract_text(data)
        except pdftext.PdfError as e:
            return {"ok": False, "error": str(e)}
        meta = self.meta() or {}
        n = self._store(text, meta.get("filename", "cv.pdf"), len(data), hashlib.sha256(data).hexdigest())
        return {"ok": True, "found": n}

    def from_text(self, text: str) -> dict:
        """The fallback for a scanned CV: the owner pastes the text. The PDF must already be saved."""
        if not self.path.exists():
            return {"ok": False, "error": "Upload your CV as a PDF first."}
        text = (text or "")[:MAX_TEXT]
        if len(text.strip()) < 20:
            return {"ok": False, "error": "Paste the text of your CV."}
        meta = self.meta() or {}
        n = self._store(text, meta.get("filename", "cv.pdf"), meta.get("size", 0), meta.get("sha256", ""))
        return {"ok": True, **self.page_data(), "found": n}

    def _store(self, text: str, filename: str, size: int, sha: str) -> int:
        today = date.fromtimestamp(self.d.clock())
        facts = extract_facts(text, today)
        with self.d.db.tx() as c:
            ticked = {(r["kind"], r["text"].lower()) for r in c.execute("SELECT kind, text FROM cv_facts WHERE ticked=1")}
            c.execute("DELETE FROM cv_facts WHERE source='cv'")
            owner = {(r["kind"], r["text"].lower()) for r in c.execute("SELECT kind, text FROM cv_facts")}
            pos = 0
            for kind, value in facts:
                if (kind, value.lower()) in owner:
                    continue
                c.execute("INSERT OR IGNORE INTO cv_facts(kind, text, ticked, source, pos) VALUES(?, ?, ?, 'cv', ?)",
                          (kind, value, int((kind, value.lower()) in ticked), pos))
                pos += 1
            c.execute("INSERT INTO cv_meta(id, filename, size, sha256, chars, uploaded_ts) VALUES(1, ?, ?, ?, ?, ?) "
                      "ON CONFLICT(id) DO UPDATE SET filename=excluded.filename, size=excluded.size, sha256=excluded.sha256, "
                      "chars=excluded.chars, uploaded_ts=excluded.uploaded_ts", (filename, size, sha, len(text), self.d.clock()))
        self.d.db.event("cv", f"CV read: {len(facts)} facts found", self.d.clock)
        return len(facts)

    # ---- the facts list ----
    def meta(self) -> dict | None:
        rows = self.d.db.query("SELECT filename, size, sha256, chars, uploaded_ts FROM cv_meta WHERE id=1")
        return rows[0] if rows else None

    def facts(self) -> list[dict]:
        return self.d.db.query("SELECT id, kind, text, ticked, source FROM cv_facts ORDER BY "
                               "CASE kind " + " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(KINDS)) + " END, source, pos, id")

    def set_tick(self, fact_id: int, ticked: bool) -> bool:
        with self.d.db.tx() as c:
            return c.execute("UPDATE cv_facts SET ticked=? WHERE id=?", (int(bool(ticked)), int(fact_id))).rowcount == 1

    def set_ticks(self, ids, ticked: bool) -> int:
        with self.d.db.tx() as c:
            return sum(c.execute("UPDATE cv_facts SET ticked=? WHERE id=?", (int(bool(ticked)), int(i))).rowcount for i in ids)

    def add_fact(self, kind: str, text: str) -> dict:
        """A fact the owner types. It starts ticked: the owner wrote it."""
        text = _clean(text or "")
        if kind not in KINDS:
            return {"ok": False, "error": "Choose a kind: " + ", ".join(KINDS) + "."}
        if len(text) < 2:
            return {"ok": False, "error": "Type the fact."}
        with self.d.db.tx() as c:
            c.execute("INSERT INTO cv_facts(kind, text, ticked, source, pos) VALUES(?, ?, 1, 'owner', ?) "
                      "ON CONFLICT(kind, text) DO UPDATE SET ticked=1",
                      (kind, text, int(self.d.clock())))
        return {"ok": True}

    def remove_fact(self, fact_id: int) -> bool:
        with self.d.db.tx() as c:
            return c.execute("DELETE FROM cv_facts WHERE id=?", (int(fact_id),)).rowcount == 1

    def claimable(self) -> dict[str, list[str]]:
        """For the writer (DJ06): ticked facts only, by kind. Nothing else from the CV."""
        out: dict[str, list[str]] = {k: [] for k in KINDS}
        for f in self.facts():
            if f["ticked"]:
                out[f["kind"]].append(f["text"])
        return out

    def ticked_count(self) -> int:
        return sum(1 for f in self.facts() if f["ticked"])

    def page_data(self) -> dict:
        facts = self.facts()
        return {"cv": self.meta(), "facts": facts, "ticked": sum(1 for f in facts if f["ticked"]), "kinds": list(KINDS)}
