"""Arbeitnow public job board API, page 1. Allowed with conditions: "please do not abuse", a link back is appreciated."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "arbeitnow", "feed", "daily", 4
URL = "https://arbeitnow.com/api/job-board-api?page=1"


def requests(opts: dict) -> list[dict]:
    return [{"scope": "arbeitnow", "url": URL, "ctx": {}}]


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)

    def make(j):
        desc, hidden = common.text_of(j.get("description") or "")
        return common.job(NAME, j.get("slug") or j.get("url"), j.get("company_name"), j.get("title"), j.get("location"),
                          common.remote_flag(explicit=j["remote"]) if "remote" in j else 0, j.get("created_at"), j.get("url"),
                          j.get("url"), desc, hidden)
    return {"jobs": common.collect(data.get("data") if isinstance(data, dict) else None, make)}
