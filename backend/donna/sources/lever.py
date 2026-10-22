"""Lever postings API, US host. Public GET (README: "may be scraped by third parties"). Never the apply POST."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "lever", "ats", "daily", None
HOST = "api.lever.co"

CAREERS = "https://jobs.lever.co/{}"      # the page a person opens


def board_url(token: str) -> str:
    return f"https://{HOST}/v0/postings/{common.q(token)}?mode=json"


def make_parser(name: str):
    def parse(text: str, ctx: dict) -> dict:
        data = common.load_json(text)
        company = ctx.get("company", "")

        def make(p):
            cats = p.get("categories") or {}
            loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
            body = p.get("descriptionPlain") or ""
            hidden = 0
            if not body and p.get("description"):
                body, hidden = common.text_of(p["description"])
            for part in p.get("lists") or []:
                if isinstance(part, dict):
                    t, h = common.text_of(part.get("content") or "")
                    body += "\n\n" + common.plain(part.get("text")) + "\n" + t
                    hidden += h
            body += "\n\n" + (p.get("additionalPlain") or "")
            wt = (p.get("workplaceType") or "").lower()
            remote = common.remote_flag(explicit=(wt == "remote")) if wt else common.remote_flag(loc, p.get("text"))
            return common.job(name, p.get("id"), company, p.get("text"), loc, remote, p.get("createdAt"), p.get("hostedUrl"),
                              p.get("applyUrl") or p.get("hostedUrl"), body.strip(), hidden, {"commitment": cats.get("commitment", "")})
        return {"jobs": common.collect(data if isinstance(data, list) else None, make)}
    return parse


parse = make_parser(NAME)
