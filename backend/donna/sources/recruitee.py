"""Recruitee careers site API. Terms unverified: at most once a day (PLAN-FINAL row 34). Never the application POST."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "recruitee", "ats", "once_a_day", None

CAREERS = "https://{}.recruitee.com"      # the page a person opens


def board_url(token: str) -> str:
    return f"https://{common.q(token)}.recruitee.com/api/offers/"


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)
    company = ctx.get("company", "")

    def make(j):
        loc = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        desc, hidden = common.text_of((j.get("description") or "") + "\n" + (j.get("requirements") or ""))
        return common.job(NAME, j.get("id") or j.get("slug"), j.get("company_name") or company, j.get("title"), loc,
                          common.remote_flag(explicit=j["remote"]) if "remote" in j else common.remote_flag(loc),
                          j.get("published_at") or j.get("created_at"), j.get("careers_url"),
                          j.get("careers_apply_url") or j.get("careers_url"), desc, hidden)
    return {"jobs": common.collect(data.get("offers") if isinstance(data, dict) else None, make)}
