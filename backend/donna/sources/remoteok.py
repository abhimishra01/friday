"""RemoteOK public API. Allowed with conditions: link back to Remote OK and name it as the source; no logo."""
from __future__ import annotations

from . import common

NAME, KIND, TIER, MAX_CALLS = "remoteok", "feed", "daily", 4
URL = "https://remoteok.com/api"


def requests(opts: dict) -> list[dict]:
    return [{"scope": "remoteok", "url": URL, "ctx": {}}]


def parse(text: str, ctx: dict) -> dict:
    data = common.load_json(text)

    def make(j):
        if "legal" in j and "position" not in j:          # the first item is the notice
            return None
        desc, hidden = common.text_of(j.get("description") or "")
        return common.job(NAME, j.get("id") or j.get("slug"), j.get("company"), j.get("position"), j.get("location") or "Remote",
                          1, j.get("date") or j.get("epoch"), j.get("url"), j.get("apply_url") or j.get("url"), desc, hidden)
    return {"jobs": common.collect(data if isinstance(data, list) else None, make)}
