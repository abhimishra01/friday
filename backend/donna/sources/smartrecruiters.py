"""SmartRecruiters posting API, read with no key. Terms unverified (docs mention a key): at most once a day, read lightly."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "smartrecruiters", "ats", "once_a_day", None

CAREERS = "https://careers.smartrecruiters.com/{}"      # the page a person opens


def board_url(token: str, country: str = "") -> str:
    # One page: a big company has thousands of postings and Donna needs one light read. The person's country code, when set, narrows it.
    c = f"country={common.q(country)}&" if country else ""
    return f"https://api.smartrecruiters.com/v1/companies/{common.q(token)}/postings?{c}limit=100"


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)
    token = ctx.get("token", "")

    def make(j):
        l = j.get("location") or {}
        loc = l.get("fullLocation") or ", ".join(x for x in (l.get("city"), l.get("region"), (l.get("country") or "").upper()) if x)
        url = f"https://jobs.smartrecruiters.com/{common.q(token)}/{common.q(str(j.get('id') or ''))}"
        return common.job(NAME, j.get("id") or j.get("uuid"), (j.get("company") or {}).get("name") or ctx.get("company", ""),
                          j.get("name"), loc, common.remote_flag(explicit=l["remote"]) if "remote" in l else common.remote_flag(loc),
                          j.get("releasedDate"), url, url, "", 0, {"level": (j.get("experienceLevel") or {}).get("label", "")})
    return {"jobs": common.collect(data.get("content") if isinstance(data, dict) else None, make)}
