"""Greenhouse job board API. Public GET, no key (JOB-SOURCES section 2). Never the application POST."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "greenhouse", "ats", "daily", None

CAREERS = "https://job-boards.greenhouse.io/{}"      # the page a person opens


def board_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{common.q(token)}/jobs?content=true"


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)
    company = ctx.get("company", "")

    def make(j):
        loc = (j.get("location") or {}).get("name", "") if isinstance(j.get("location"), dict) else ""
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [] if isinstance(o, dict) and o.get("name"))
        desc, hidden = common.text_of(j.get("content") or "")
        return common.job(NAME, j.get("id"), j.get("company_name") or company, j.get("title"), loc or offices,
                          common.remote_flag(loc, offices, j.get("title")), j.get("first_published") or j.get("updated_at"),
                          j.get("absolute_url"), j.get("absolute_url"), desc, hidden)
    return {"jobs": common.collect(data.get("jobs") if isinstance(data, dict) else None, make)}
