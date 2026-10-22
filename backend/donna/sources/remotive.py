"""Remotive public API, software-dev category. Allowed with conditions: link back and name Remotive; at most 4 calls a day
(their notice); jobs are 24 hours late; never passed to other job boards."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "remotive", "feed", "daily", 4
URL = "https://remotive.com/api/remote-jobs?category=software-dev"


def requests(opts: dict) -> list[dict]:
    return [{"scope": "remotive", "url": URL, "ctx": {}}]


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)

    def make(j):
        desc, hidden = common.text_of(j.get("description") or "")
        return common.job(NAME, j.get("id"), j.get("company_name"), j.get("title"), j.get("candidate_required_location") or "Remote",
                          1, j.get("publication_date"), j.get("url"), j.get("url"), desc, hidden, {"salary": j.get("salary", "")})
    return {"jobs": common.collect(data.get("jobs") if isinstance(data, dict) else None, make)}
