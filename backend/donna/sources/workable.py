"""Workable public careers widget. Terms unverified: at most once a day (PLAN-FINAL row 34)."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "workable", "ats", "once_a_day", None

CAREERS = "https://apply.workable.com/{}"      # the page a person opens


def board_url(token: str) -> str:
    return f"https://apply.workable.com/api/v1/widget/accounts/{common.q(token)}"


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)
    company = (data.get("name") if isinstance(data, dict) else "") or ctx.get("company", "")

    def make(j):
        locs = j.get("locations") or []
        if locs and isinstance(locs[0], dict):
            loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x) for l in locs if isinstance(l, dict))
        else:
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        desc, hidden = common.text_of(j.get("description") or "")
        return common.job(NAME, j.get("shortcode") or j.get("code") or j.get("id"), company, j.get("title"), loc,
                          common.remote_flag(explicit=j["telecommuting"]) if "telecommuting" in j else common.remote_flag(loc),
                          j.get("published_on") or j.get("created_at"), j.get("url") or j.get("shortlink"),
                          j.get("application_url") or j.get("url"), desc, hidden, {"employment": j.get("employment_type", "")})
    return {"jobs": common.collect(data.get("jobs") if isinstance(data, dict) else None, make)}
