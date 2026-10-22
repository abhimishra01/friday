"""Facts from a job post, by code (DJ05, House H7). The writer and the scorer see these facts, never the raw post.

The fetch worker already removed hidden elements, off-screen and same-colour text and zero-width characters (sources/common).
This module cleans again (a post may come in as HTML), then reads: title, company, location, remote, stack words, requirement
lines, years asked, pay, apply addresses with the sentence around them, and the poster's notes (no recruiters, no agencies,
apply via the form, a subject format, US only, work authorisation). Two checks stop a post:
- instruction-like text ("if you are an AI", "include the word") flags it for the owner: no score, no draft;
- a request for money, a starter kit or bank details drops it.
Everything here reads untrusted text. It only matches patterns; nothing in a post can change what Friday does.
"""
from __future__ import annotations

import re

from .dedupe import norm_company, norm_title
from .sources import common
from .sources.careers import ROLE_LOCALS

MAX_REQUIREMENTS = 8
MAX_LINE = 160
MAX_SUBJECT = 100

STACK_WORDS = (
    "python", "javascript", "typescript", "react", "react native", "next.js", "node.js", "node", "express", "nestjs", "vue", "angular",
    "svelte", "redux", "graphql", "rest", "grpc", "django", "flask", "fastapi", "rails", "ruby", "php", "laravel", "go", "golang",
    "rust", "java", "kotlin", "scala", "c#", ".net", "c++", "elixir", "swift", "postgres", "postgresql", "mysql", "sqlite", "mongodb",
    "redis", "elasticsearch", "kafka", "rabbitmq", "dynamodb", "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "linux",
    "ci/cd", "html", "css", "tailwind", "llm", "llms", "rag", "langchain", "openai", "pytorch", "tensorflow", "machine learning",
    "microservices", "serverless", "websockets", "sql")
_STACK = [(w, re.compile(r"(?<![\w.#+])" + re.escape(w) + r"(?![\w#+]|\.\w)", re.I)) for w in STACK_WORDS]
_CANON = {"golang": "go", "postgresql": "postgres", "node.js": "node", "llms": "llm"}

# Only the bracketed forms people write on purpose ("jobs [at] acme [dot] com"). A bare " at " is never read as @.
_BRACKET_EMAIL = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63})\s*[\[({<]\s*at\s*[\])}>]\s*([a-z0-9-]{1,63}(?:\s*(?:[\[({<]\s*dot\s*[\])}>]|\.)\s*[a-z0-9-]{1,63})+)", re.I)
_BRACKET_DOT = re.compile(r"\s*(?:[\[({<]\s*dot\s*[\])}>]|\.)\s*", re.I)
HIRING_LOCALS = ROLE_LOCALS + ("recruitment", "join", "apply", "work", "people", "hr")
_INTENT = re.compile(r"\b(?:apply|applications?|send|email|e-mail|mail|cv|resume|résumé|reach\s+out|contact|write\s+to|drop\s+(?:us|me)|"
                     r"get\s+in\s+touch|interested|introduce\s+yourself|hiring|portfolio)\b", re.I)
_NOT_HIRING_LOCALS = re.compile(r"^(?:no-?reply|do-?not-?reply|privacy|security|abuse|legal|dpo|gdpr|billing|invoices?|accounts|press|media|"
                                r"sales|marketing|support|help|webmaster|postmaster|admin|unsubscribe)$", re.I)

_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
_PAY = re.compile(r"(?:[$€£₹]\s?\d[\d,.]*\s?[kKmM]?(?:\s?(?:-|–|to)\s?[$€£₹]?\s?\d[\d,.]*\s?[kKmM]?)?(?:\s?(?:usd|eur|gbp|inr))?"
                  r"|\b\d[\d,.]*\s?(?:k|lpa|lakhs?|usd|eur|gbp|inr)\b(?:\s?(?:-|–|to)\s?\d[\d,.]*\s?(?:k|lpa|lakhs?|usd|eur|gbp|inr)?)?)", re.I)
_REQ_CUE = re.compile(r"\b(?:experience|years?|proficien\w*|familiar\w*|knowledge\s+of|expert\w*|strong|solid|must|required|requirements?|"
                      r"you\s+have|you've|skills?|comfortable\s+with|background\s+in|degree|fluent)\b", re.I)

_SUBJECT = re.compile(r"\bsubject(?:\s+line)?\s*(?:should\s+be|must\s+be|must\s+read|format|of|as|like|:|-)?\s*[:\-]?\s*"
                      r"[\"“'‘]([^\"”'’\n]{3,140})[\"”'’]", re.I)
_SUBJECT_BARE = re.compile(r"\b(?:with|use)\s+(?:the\s+)?subject(?:\s+line)?\s*:\s*([^\n.]{3,120})", re.I)

# Location limits (the hard filter drops these).
US_ONLY = re.compile(r"\b(?:us|u\.s\.?|usa|united\s+states)[\s-]*(?:only|based\s+only|residents\s+only|citizens\s+only)\b"
                     r"|\bonly\s+(?:open\s+to\s+)?(?:candidates\s+)?(?:in|from|based\s+in)\s+the\s+(?:us|u\.s\.?|usa|united\s+states)\b"
                     r"|\bmust\s+(?:be\s+)?(?:located|based|reside|living|live)\s+in\s+the\s+(?:us|u\.s\.?|usa|united\s+states|continental\s+us)\b"
                     r"|\b(?:within|in)\s+(?:the\s+)?(?:us|u\.s\.?|usa)\s+time\s?zones?\s+only\b|\bnorth\s+america\s+only\b", re.I)
WORK_AUTH = re.compile(r"\b(?:authori[sz]ed|authori[sz]ation|eligible|eligibility|right)\s+to\s+work\s+in\s+(?:the\s+)?"
                       r"(?:us|u\.s\.?|usa|united\s+states|uk|united\s+kingdom|eu|european\s+union|canada|australia|germany|europe)\b"
                       r"|\b(?:us|u\.s\.?|usa|uk|eu)\s+(?:citizens?|citizenship|persons?|work\s+authori[sz]ation|work\s+permit)\s+(?:only|required)\b"
                       r"|\bmust\s+be\s+a\s+(?:us|u\.s\.?|uk)\s+(?:citizen|person|national)\b|\bsecurity\s+clearance\b|\bgreen\s+card\b"
                       r"|\b(?:valid|existing)\s+(?:us|uk|eu)\s+work\s+(?:visa|permit|authori[sz]ation)\b", re.I)

# Money asks: a real employer never asks for these (scam).
MONEY_ASK = re.compile(r"\bstarter\s+kit\b|\b(?:registration|training|onboarding|processing|application|equipment|joining)\s+(?:fee|charges?|deposit)\b"
                       r"|\bsecurity\s+deposit\b|\brefundable\s+deposit\b|\bupfront\s+(?:fee|payment|cost)\b"
                       r"|\bbank\s+(?:account\s+)?(?:details|information|info|number|login)\b|\baccount\s+and\s+routing\b|\brouting\s+number\b"
                       r"|\bifsc\b|\bgift\s+cards?\b|\bwestern\s+union\b|\bmoneygram\b|\bsend\s+(?:us\s+)?(?:money|payment|bitcoin|crypto)\b"
                       r"|\b(?:pay|purchase|buy)\s+(?:for\s+)?(?:your\s+own\s+|the\s+)?(?:equipment|laptop|kit|software\s+licen[cs]e|training)\b", re.I)


def clean(raw: str) -> tuple[str, int, bool]:
    """(clean text, hidden elements removed, hidden instructions found). HTML goes through the same strip as the worker."""
    s = raw or ""
    hidden = 0
    if re.search(r"<[a-z!/][^>]*>", s, re.I):
        s, _links, hidden = common.html_to_text(s)
    else:
        s = common._ZERO_WIDTH.sub("", s).replace("\xa0", " ")
    flagged = common.HIDDEN_FLAG in s
    s = s.replace(common.HIDDEN_FLAG, "").strip()
    return s, hidden, flagged


def one_line(s: str, limit: int = MAX_LINE) -> str:
    s = common.EMAIL_ADDRESS.sub("[address]", s)
    s = re.sub(r"https?://\S+|www\.\S+", "[link]", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -•*·\t")[:limit]


def _sentence_around(text: str, start: int, end: int) -> str:
    a = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start))
    b_candidates = [i for i in (text.find("\n", end), text.find(". ", end)) if i >= 0]
    b = min(b_candidates) if b_candidates else len(text)
    return re.sub(r"\s+", " ", text[a + 1:b]).strip()[:240]


def emails(text: str) -> list[dict]:
    """Every address written in the visible text, with the sentence around it and whether that sentence is about applying."""
    out: dict[str, dict] = {}
    for m in common.EMAIL_ADDRESS.finditer(text):
        addr = f"{m.group(1)}@{m.group(2)}".lower().rstrip(".")
        out.setdefault(addr, {"address": addr, "context": _sentence_around(text, m.start(), m.end()), "written": "plain"})
    for m in _BRACKET_EMAIL.finditer(text):
        domain = _BRACKET_DOT.sub(".", m.group(2)).lower().strip(".")
        addr = f"{m.group(1).lower()}@{domain}"
        if common.EMAIL_ADDRESS.fullmatch(addr):
            out.setdefault(addr, {"address": addr, "context": _sentence_around(text, m.start(), m.end()), "written": "bracketed"})
    for e in out.values():
        local = e["address"].split("@", 1)[0]
        e["hiring"] = not _NOT_HIRING_LOCALS.match(local) and bool(_INTENT.search(common.EMAIL_ADDRESS.sub(" ", e["context"])) or local in HIRING_LOCALS)
    return list(out.values())


def stack(text: str) -> list[str]:
    found = []
    for w, rx in _STACK:
        if rx.search(text):
            c = _CANON.get(w, w)
            if c not in found:
                found.append(c)
    if "node" in found and not re.search(r"\bnode(?:\.?js)?\b(?!\s+(?:of|in|on))", text, re.I):
        found.remove("node")
    return found


def requirements(text: str) -> list[str]:
    out = []
    for line in re.split(r"\n+|(?<=[.;])\s+(?=[A-Z])|\s+[•·]\s+", text):
        line = line.strip()
        if len(line) < 12 or common.INSTRUCTION.search(line):
            continue
        if _REQ_CUE.search(line) or any(rx.search(line) for _w, rx in _STACK[:40]):
            one = one_line(line)
            if one and one not in out:
                out.append(one)
        if len(out) >= MAX_REQUIREMENTS:
            break
    return out


def years_asked(text: str) -> int | None:
    vals = [int(m.group(1)) for m in _YEARS.finditer(text) if 0 < int(m.group(1)) <= 25
            and re.search(r"experience|exp\b|professional|industry|building|working|writing|shipping", text[m.end():m.end() + 60], re.I)]
    return min(vals) if vals else None


def subject_format(text: str) -> str:
    m = _SUBJECT.search(text) or _SUBJECT_BARE.search(text)
    if not m:
        return ""
    s = one_line(m.group(1), MAX_SUBJECT)
    return "" if ("[address]" in s or "[link]" in s or common.INSTRUCTION.search(s)) else s


def extract(job: dict) -> dict:
    """Facts for the filters, the scorer, the router and the writer. `job` is a live row from the job store (or the same shape)."""
    text, hidden_here, hidden_flag = clean(job.get("description") or "")
    extra = job.get("extra") or {}
    header = common.plain(extra.get("header", "")) if isinstance(extra, dict) else ""
    title = common.plain(job.get("title"))
    if job.get("source") == "hn" and header and title.startswith("Open roles"):
        title = header.split("|", 2)[1].strip() if header.count("|") >= 1 else title
    whole = "\n".join(x for x in (title, header, common.plain(job.get("location")), text) if x)
    flags = []
    if hidden_flag:
        flags.append("Hidden text in this post held instructions for a model.")
    if common.INSTRUCTION.search(whole):
        flags.append("This post has instruction-like text aimed at a model.")
    pay = ""
    m = _PAY.search(" ".join(x for x in (extra.get("salary", "") if isinstance(extra, dict) else "", text) if x))
    if m:
        pay = m.group(0).strip()[:60]
    title_norm, seniority = norm_title(title)
    return {
        "job_id": job.get("id"), "source": job.get("source", ""), "sources": list(job.get("sources") or [job.get("source", "")]),
        "title": title, "title_norm": title_norm, "seniority": seniority, "company": common.plain(job.get("company")),
        "company_norm": norm_company(job.get("company", "")), "location": common.plain(job.get("location")), "header": header,
        "remote": int(job.get("remote") or 0), "posted": job.get("posted") or "", "first_seen": job.get("first_seen") or "",
        "job_url": job.get("job_url", ""), "apply_url": job.get("apply_url", ""),
        "stack": stack(whole), "requirements": requirements(text), "years": years_asked(text), "pay": pay,
        "emails": emails(text), "subject_format": subject_format(text),
        "notes": {"no_recruiters": bool(common.NO_RECRUITERS.search(whole)), "no_agencies": bool(common.NO_AGENCIES.search(whole)),
                  "form_only": bool(common.FORM_ONLY.search(whole)), "us_only": bool(US_ONLY.search(whole)),
                  "work_auth": bool(WORK_AUTH.search(whole)), "no_sponsorship": bool(re.search(r"\bno\s+(?:visa\s+)?sponsorship\b", whole, re.I))},
        "money_ask": bool(MONEY_ASK.search(whole)), "flags": flags,
        "hidden_removed": int(job.get("hidden") or 0) + hidden_here,
        "_text": text,               # for the router and the location filter only; never given to the writer or the scorer
    }


def for_writer(facts: dict) -> dict:
    """The facts the writer (DJ06) and the scorer may see: no raw text, no addresses, no links."""
    keep = ("title", "company", "location", "remote", "seniority", "stack", "requirements", "years", "pay", "subject_format")
    return {k: facts[k] for k in keep}
