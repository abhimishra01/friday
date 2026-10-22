"""Friday's job sources: one module per source. Standard library only and relative imports only, because the fetch worker
loads this package by file path under `python -I` (it imports nothing from The Office). The parsers run in the worker.

Each module has NAME, KIND (ats, feed, hn, manual), TIER (daily, once_a_day, manual) and MAX_CALLS (calls a day, None for
one call per board). An ATS module has `board_url(token)` and CAREERS (the page a person opens); a feed has
`requests(opts)`; all have `parse(text, ctx)`.
`sources.yaml` next to this folder records endpoint, terms, date checked, rate limit and attribution for each one.
"""
from __future__ import annotations

import importlib

ATS = ("greenhouse", "lever", "lever_eu", "ashby", "workable", "smartrecruiters", "recruitee")
FEEDS = ("himalayas", "weworkremotely", "remoteok", "remotive", "arbeitnow")
HN = ("hn",)
MANUAL = ("linkedin", "indeed", "naukri", "upwork", "wellfound", "yc", "contra", "jooble")
FETCHED = ATS + FEEDS + HN
# Parser names the worker accepts. "hn_find" is the thread search; "careers" reads a company's own careers or contact page
# for published role addresses (DJ05); the rest are module names.
PARSERS = FETCHED + ("hn_find", "careers")

# Keep the best copy of a job: the company's own board first, then HN, then the feeds (JOB-SOURCES 4.2).
RANK = {**{a: 0 for a in ATS}, "hn": 1, **{f: 2 for f in FEEDS}}


def module(name: str):
    if name not in FETCHED + ("manual",):
        raise KeyError(name)
    return importlib.import_module(f"{__name__}.{name}")


def parser(name: str):
    if name == "hn_find":
        return module("hn").parse_find
    if name == "careers":
        return importlib.import_module(f"{__name__}.careers").parse
    if name not in FETCHED:
        raise KeyError(name)
    return module(name).parse
