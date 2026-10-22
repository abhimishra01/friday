"""The owner's private blocklist: `data/Friday/blocklist.txt`, edited on the page (Settings, `/api/Friday/blocklist`).

One rule a line: a domain (`example.com`, which also blocks its subdomains), a company name (`name: Example Ltd`) or a job-board
token (`board: example`, the company's name on Greenhouse, Lever and the like). The name and the token catch a role that is posted
on a shared job board and answered through a shared hiring-system address (DJ10 M4). Lines that start with # are notes.

No name or domain is ever written in code or tests (tests use `employer.invalid`). Reason: The Office is open source and must not
name the owner's employer (PLAN-FINAL row 42).
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from .dedupe import norm_company

MAX_BYTES = 64 * 1024
MAX_LINES = 2000
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")


def normalise(value: str) -> str:
    """A domain from a domain, an email address or a web address. Empty when it is not one."""
    v = (value or "").strip().lower()
    if "@" in v and "://" not in v:
        v = v.rsplit("@", 1)[1]
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v)
    v = re.split(r"[/?#]", v, maxsplit=1)[0]
    v = v.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    if v.startswith("www."):
        v = v[4:]
    try:
        v = v.encode("idna").decode("ascii") if v and not v.isascii() else v
    except UnicodeError:
        return ""
    return v if _DOMAIN_RE.match(v) else ""


def compact(value: str) -> str:
    """Letters and digits only, lower case: "Example, Inc." and "example-inc" compare equal."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


class Rules(NamedTuple):
    domains: list[str]
    names: list[str]          # company names as the owner wrote them
    boards: list[str]         # job-board tokens, lower case


def parse_rules(text: str) -> tuple[Rules, list[str]]:
    """(rules, lines that are no rule). Notes and blank lines are skipped. No duplicates, order kept."""
    domains, names, boards, bad = [], [], [], []
    for line in (text or "").splitlines()[:MAX_LINES]:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, rest = line.partition(":")
        key, rest = key.strip().lower(), rest.strip()
        if sep and key == "name" and not rest.startswith("//"):
            if not 2 <= len(compact(rest)) <= 80 or len(rest) > 100:
                bad.append(line[:100])
            elif rest not in names:
                names.append(rest)
        elif sep and key == "board" and not rest.startswith("//"):
            tok = rest.lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,60}", tok):
                bad.append(line[:100])
            elif tok not in boards:
                boards.append(tok)
        else:
            d = normalise(line)
            if not d:
                bad.append(line[:100])
            elif d not in domains:
                domains.append(d)
    return Rules(domains, names, boards), bad


def parse(text: str) -> tuple[list[str], list[str]]:
    """(domains, lines that are no rule). The company names and board tokens are in `parse_rules`."""
    rules, bad = parse_rules(text)
    return rules.domains, bad


def brand(domain: str) -> str:
    """The name part of a domain, compact: "second-employer.invalid" gives "secondemployer"; a name under a country second level loses that level. Four letters at least,
    or nothing. A blocked domain also blocks a company of that name on a shared job board."""
    parts = domain.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov", "edu"):
        parts = parts[:-1]
    word = compact(parts[-2]) if len(parts) >= 2 else ""
    return word if len(word) >= 4 else ""


def board_tokens(url: str) -> set[str]:
    """Words of a job link that can name the company on a job board: the first host label and the first two path segments."""
    try:
        u = urlsplit(url or "")
        host = (u.hostname or "").lower()
    except ValueError:
        return set()
    labels = [x for x in host.split(".")[:-2] if x not in ("www", "boards", "job-boards", "jobs", "careers", "apply")]
    path = [x for x in (u.path or "").lower().split("/") if x][:2]
    return {compact(x) for x in labels[:1] + path if len(compact(x)) >= 2}


def blocked(value: str, domains) -> bool:
    """True when the domain (or the domain of an address or link) is on the list, or is a subdomain of one."""
    d = normalise(value)
    return bool(d) and any(d == b or d.endswith("." + b) for b in domains)


class Blocklist:
    def __init__(self, path: Path):
        self.path = Path(path)

    def text(self) -> str:
        """The file as the owner wrote it, for the page."""
        try:
            fd = os.open(str(self.path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return ""
        with os.fdopen(fd, "rb") as fh:
            return fh.read(MAX_BYTES).decode("utf-8", "replace")

    def rules(self) -> Rules:
        return parse_rules(self.text())[0]

    def domains(self) -> list[str]:
        return self.rules().domains

    def company_blocked(self, company: str = "", urls=(), addresses=(), rules: Rules | None = None) -> bool:
        """True when the company name, a link, or an address matches a rule. An address on a shared hiring-system domain
        is matched on the words of its local part and its subdomain, because its domain says nothing about the company."""
        rules = rules or self.rules()
        names = {compact(norm_company(n)) or compact(n) for n in rules.names}
        boards = {compact(b) for b in rules.boards} | {x for x in (brand(d) for d in rules.domains) if x}
        cn = compact(norm_company(company)) or compact(company)
        if cn and (cn in names or cn in boards):
            return True
        words: set[str] = set()
        for u in urls:
            if u and blocked(u, rules.domains):
                return True
            words |= board_tokens(u)
        for a in addresses:
            if a and blocked(a, rules.domains):
                return True
            local, _, host = str(a).rpartition("@")
            words |= {compact(x) for x in [local, *host.split(".")[:-2]] if compact(x)}
        return bool(words & (names | boards))

    def is_blocked(self, value: str) -> bool:
        return blocked(value, self.domains())

    def save(self, text: str) -> dict:
        """Check every line, then write the domains (one a line) to an owner-only file. Nothing is written when a line is wrong."""
        if len((text or "").encode("utf-8")) > MAX_BYTES:
            return {"ok": False, "error": "The list is too long."}
        rules, bad = parse_rules(text)
        if bad:
            return {"ok": False, "error": "These lines are not rules: " + ", ".join(bad[:5]) + ". Write one rule a line: a domain "
                    "(example.com), name: Company Name, or board: token."}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp = self.path.parent / f".blocklist-{secrets.token_hex(8)}.tmp"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("".join(d + "\n" for d in rules.domains) + "".join(f"name: {n}\n" for n in rules.names)
                         + "".join(f"board: {b}\n" for b in rules.boards))
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        return {"ok": True, "domains": rules.domains, "names": rules.names, "boards": rules.boards}
