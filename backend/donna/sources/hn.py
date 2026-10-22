"""HN "Ask HN: Who is hiring?" through the Algolia HN API. Public API for public posts.

Two steps: `find` reads the whoishiring account's newest stories and picks this month's thread; `thread` reads the thread
once. Each top-level comment is one post. The first line is usually "Company | Role | Location | REMOTE | ...".
"""
from __future__ import annotations

import re

from . import common

NAME, KIND, TIER, MAX_CALLS = "hn", "hn", "daily", 4
FIND_URL = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{}"
POST_URL = "https://news.ycombinator.com/item?id={}"
_THREAD = re.compile(r"^\s*ask hn:\s*who is hiring\?", re.I)
_ROLE = re.compile(r"engineer|developer|programmer|scientist|designer|architect|devops|\bsre\b|full[\s-]?stack|back[\s-]?end|"
                   r"front[\s-]?end|\bml\b|\bai\b|founding|\bcto\b|\bhead of\b|\blead\b|manager|analyst|researcher", re.I)
_PLACE = re.compile(r"remote|on-?site|hybrid|in[\s-]office|anywhere|worldwide|\b(us|usa|uk|eu|emea|apac|india|europe|canada)\b|"
                    r"[A-Z][a-z]+,\s*[A-Z]{2}\b|\b(new york|nyc|london|berlin|bangalore|bengaluru|san francisco|sf|bay area)\b", re.I)


def find_request() -> dict:
    return {"scope": "hn:find", "url": FIND_URL, "ctx": {}}


def thread_request(thread_id: str) -> dict:
    return {"scope": "hn", "url": ITEM_URL.format(common.q(str(thread_id))), "ctx": {"thread": str(thread_id)}}


def parse_find(text: str, ctx: dict) -> dict:
    """meta.thread_id: the newest "Who is hiring?" story from the month in ctx (YYYY-MM), or empty."""
    data = common.load_json(text)
    month = ctx.get("month", "")
    for h in (data.get("hits") if isinstance(data, dict) else None) or []:
        if not isinstance(h, dict) or not _THREAD.match(str(h.get("title") or "")):
            continue
        if month and not str(h.get("created_at") or "").startswith(month):
            continue
        tid = str(h.get("objectID") or h.get("story_id") or "")
        if tid.isdigit():
            return {"jobs": [], "meta": {"thread_id": tid, "title": common.plain(h.get("title")), "created": common.date_of(h.get("created_at"))}}
    return {"jobs": [], "meta": {"thread_id": ""}}


def header_parts(text: str) -> list[str]:
    first = re.split(r"\n", text.strip(), maxsplit=1)[0] if text.strip() else ""
    return [p.strip() for p in first.split("|") if p.strip()] if "|" in first else ([first.strip()] if first.strip() else [])


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)
    if not isinstance(data, dict):
        return {"jobs": []}

    def make(c):
        if c.get("deleted") or c.get("dead") or not c.get("text"):
            return None
        body, links, hidden = common.html_to_text(c["text"])
        parts = header_parts(body)
        if not parts:
            return None
        company = parts[0]
        rest = parts[1:]
        title = next((p for p in rest if _ROLE.search(p)), "")
        places = [p for p in rest if p != title and _PLACE.search(p)]
        if not title:
            title = "Open roles (see the post)"
        header = " | ".join(parts)
        post = POST_URL.format(c.get("id"))
        apply = next((u for u in links if re.match(r"^https?://", u, re.I) and "ycombinator.com" not in u.lower()), "") \
            or common.first_url(body, ("ycombinator.com",)) or post
        return common.job(NAME, c.get("id"), company, title, " | ".join(places), common.remote_flag(header),
                          c.get("created_at") or c.get("created_at_i"), post, apply, body, hidden,
                          {"header": header, "thread": ctx.get("thread", "")})
    return {"jobs": common.collect(data.get("children"), make)}
