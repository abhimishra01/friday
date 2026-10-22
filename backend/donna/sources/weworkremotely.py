"""We Work Remotely RSS (full-stack, back-end, front-end). Allowed with conditions: attribute and link back to WWR;
applying goes through the WWR site."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from . import common

NAME, KIND, TIER, MAX_CALLS = "weworkremotely", "feed", "daily", 12
CATEGORIES = ("full-stack", "back-end", "front-end")
URL = "https://weworkremotely.com/categories/remote-{}-programming-jobs.rss"


def requests(opts: dict) -> list[dict]:
    return [{"scope": f"weworkremotely:{c}", "url": URL.format(c), "ctx": {}} for c in CATEGORIES]


def safe_xml(text: str):
    """No DTD, no entities: a feed with either is refused (entity expansion is how XML bombs work)."""
    head = text.lstrip("﻿ \t\r\n")
    if re.search(r"<!DOCTYPE|<!ENTITY", head, re.I):
        raise ValueError("feed has a DTD")
    return ET.fromstring(head.encode("utf-8"))


def parse(text: str, ctx: dict) -> dict:
    root = safe_xml(text)
    items = []
    for it in root.iter("item"):
        items.append({c.tag.split("}")[-1]: (c.text or "") for c in it})

    def make(i):
        title = i.get("title", "")
        company, _, role = title.partition(":")
        if not role:
            company, role = "", title
        desc, hidden = common.text_of(i.get("description", ""))
        loc = i.get("region") or i.get("country") or "Remote"
        link = i.get("link") or i.get("guid")
        return common.job(NAME, i.get("guid") or link, company, role, loc, 1, i.get("pubDate"), link, link, desc, hidden)
    return {"jobs": common.collect(items, make)}
