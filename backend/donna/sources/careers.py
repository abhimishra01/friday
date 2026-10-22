"""A company's own careers or contact page (DJ05). Runs in the fetch worker, like every parser.

It returns only what a person can see: role addresses written in the visible text or in a visible mailto link, a few page
notes, and links to the site's careers and contact pages. Never an address from a script, a hidden element, an image or a
decoded trick. It never guesses an address. The Office checks the domains (route.py).
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlsplit

from . import common

NAME = "careers"
ROLE_LOCALS = ("careers", "jobs", "hiring", "talent", "recruiting")
_PAGE_LINK = re.compile(r"careers?|jobs?|join(?:-us)?|work-with-us|contact", re.I)
_UNSOLICITED = re.compile(r"\b(?:no|do\s+not\s+accept|don't\s+accept|not\s+accepting|we\s+do\s+not\s+accept)\s+unsolicited\b", re.I)
MAX_ADDRESSES = 20
MAX_LINKS = 10


def role_addresses(text: str, links: list[str]) -> list[str]:
    """careers@, jobs@, hiring@, talent@ and recruiting@ addresses, in page order, lower case, no repeats."""
    found = [f"{m.group(1)}@{m.group(2)}" for m in common.EMAIL_ADDRESS.finditer(text or "")]
    for href in links:
        if href.lower().startswith("mailto:"):
            addr = unquote(href[7:].split("?", 1)[0]).strip()
            m = common.EMAIL_ADDRESS.fullmatch(addr)
            if m:
                found.append(addr)
    out = []
    for a in found:
        a = a.lower().rstrip(".")
        if a.split("@", 1)[0] in ROLE_LOCALS and a not in out:
            out.append(a)
    return out[:MAX_ADDRESSES]


def parse(text: str, ctx: dict) -> dict:
    body, links, hidden = common.html_to_text(text or "")
    base = str(ctx.get("url") or "")
    host = (urlsplit(base).hostname or "").lower()
    pages = []
    for href in links:
        u = urljoin(base, href) if base else href
        p = urlsplit(u)
        if p.scheme in ("http", "https") and (p.hostname or "").lower() == host and _PAGE_LINK.search(p.path or "") and u not in pages:
            pages.append(u.split("#", 1)[0])
    meta = {"addresses": role_addresses(body, links), "pages": pages[:MAX_LINKS], "hidden": hidden,
            "no_agencies": bool(common.NO_AGENCIES.search(body)), "no_recruiters": bool(common.NO_RECRUITERS.search(body)),
            "form_only": bool(common.FORM_ONLY.search(body)), "no_unsolicited": bool(_UNSOLICITED.search(body)),
            "instructions": bool(common.INSTRUCTION.search(body)) or common.HIDDEN_FLAG in body}
    return {"jobs": [], "meta": meta}
