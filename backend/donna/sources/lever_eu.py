"""Lever postings API, EU host (api.eu.lever.co). Same rules as the US host."""
from __future__ import annotations

from . import common, lever

NAME, KIND, TIER, MAX_CALLS = "lever_eu", "ats", "daily", None
HOST = "api.eu.lever.co"

CAREERS = "https://jobs.eu.lever.co/{}"      # the page a person opens


def board_url(token: str) -> str:
    return f"https://{HOST}/v0/postings/{common.q(token)}?mode=json"


parse = lever.make_parser(NAME)
