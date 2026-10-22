"""Ashby public job posting API. Terms unverified: at most once a day (PLAN-FINAL row 34)."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "ashby", "ats", "once_a_day", None

CAREERS = "https://jobs.ashbyhq.com/{}"      # the page a person opens


def board_url(token: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{common.q(token)}?includeCompensation=true"


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)
    company = ctx.get("company", "")

    def make(j):
        if j.get("isListed") is False:
            return None
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)]
        loc = ", ".join(x for x in locs if x)
        desc = j.get("descriptionPlain") or ""
        hidden = 0
        if j.get("descriptionHtml"):
            desc, hidden = common.text_of(j["descriptionHtml"])
        wt = (j.get("workplaceType") or "").lower()
        remote = common.remote_flag(explicit=bool(j.get("isRemote")) or wt == "remote") if ("isRemote" in j or wt) else common.remote_flag(loc)
        return common.job(NAME, j.get("id"), company, j.get("title"), loc, remote, j.get("publishedAt"), j.get("jobUrl"),
                          j.get("applyUrl") or j.get("jobUrl"), desc, hidden, {"employment": j.get("employmentType", "")})
    return {"jobs": common.collect(data.get("jobs") if isinstance(data, dict) else None, make)}
