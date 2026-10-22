"""Himalayas search API (the person's own country when set, and worldwide, a few keywords). Allowed with conditions: show a link to Himalayas as the
source, use applicationLink unchanged, never pass jobs to other boards, remove expired jobs."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "himalayas", "feed", "daily", 10
URL = "https://himalayas.app/jobs/api/search?q={q}&{where}"
def requests(opts: dict) -> list[dict]:
    """No keywords, no requests: the words come from the person's settings (feed keywords, else the job titles)."""
    words = [w for w in (opts.get("keywords") or []) if w][:4]
    cc = str(opts.get("country_code") or "").strip().lower()[:2]
    where = ([(cc, f"country={cc.upper()}")] if cc.isalpha() and len(cc) == 2 else []) + [("worldwide", "worldwide=true")]
    return [{"scope": f"himalayas:{w.lower()}:{tag}", "url": URL.format(q=common.q(w), where=wh), "ctx": {}}
            for w in words for tag, wh in where]


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)

    def make(j):
        locs = j.get("locationRestrictions") or []
        loc = ", ".join(str(x.get("name", x) if isinstance(x, dict) else x) for x in locs) or "Worldwide"
        tz = j.get("timezoneRestrictions") or []
        desc, hidden = common.text_of(j.get("description") or j.get("excerpt") or "")
        return common.job(NAME, j.get("guid") or j.get("applicationLink"), j.get("companyName"), j.get("title"), loc, 1,
                          j.get("pubDate"), j.get("guid") or j.get("applicationLink"), j.get("applicationLink"), desc, hidden,
                          {"timezones": ", ".join(str(t) for t in tz), "expires": common.date_of(j.get("expiryDate"))})
    return {"jobs": common.collect(data.get("jobs") if isinstance(data, dict) else None, make)}
