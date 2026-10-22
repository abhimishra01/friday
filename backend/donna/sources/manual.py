"""Sites Friday never reads by machine (their terms or robots.txt forbid it, or the terms are unknown). For each, she makes a
"search by hand" card with saved search links built from the owner's titles and locations. Nothing here fetches anything."""
from __future__ import annotations

import re
from urllib.parse import urlencode

NAME, KIND, TIER, MAX_CALLS = "manual", "manual", "manual", 0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _remote(loc: str) -> bool:
    return (loc or "").strip().lower() in ("remote", "anywhere", "worldwide", "work from home")


def linkedin(title, loc):
    p = {"keywords": title, "f_TPR": "r604800"}
    p.update({"f_WT": "2"} if _remote(loc) else {"location": loc})
    return "https://www.linkedin.com/jobs/search/?" + urlencode(p)


def indeed(title, loc, cc=""):
    return f"https://{cc + '.' if cc else 'www.'}indeed.com/jobs?" + urlencode({"q": title, "l": "Remote" if _remote(loc) else loc, "fromage": "7"})


def naukri(title, loc):
    base = f"https://www.naukri.com/{_slug(title)}-jobs"
    return base + "?" + urlencode({"wfhType": "2"}) if _remote(loc) else f"{base}-in-{_slug(loc)}"


def upwork(title, loc):
    return "https://www.upwork.com/nx/search/jobs/?" + urlencode({"q": title, "sort": "recency"})


def wellfound(title, loc):
    return f"https://wellfound.com/role/r/{_slug(title)}" if _remote(loc) else f"https://wellfound.com/role/l/{_slug(title)}/{_slug(loc)}"


def yc(title, loc):
    p = {"query": title}
    if _remote(loc):
        p["remote"] = "yes"
    return "https://www.workatastartup.com/companies?" + urlencode(p)


def contra(title, loc):
    return "https://contra.com/jobs?" + urlencode({"search": title})


def jooble(title, loc):
    return "https://jooble.org/SearchResult?" + urlencode({"ukw": title, "rgns": "Remote" if _remote(loc) else loc})


# (key, site name, why Donna does not read it, link builder, uses the location)
SITES = (
    ("linkedin", "LinkedIn", "LinkedIn forbids robots and automated access.", linkedin, True),
    ("indeed", "Indeed", "Indeed's terms bar automated tools for job listings.", indeed, True),
    ("naukri", "Naukri", "Naukri's terms forbid automated crawling.", naukri, True),
    ("upwork", "Upwork", "Upwork forbids scraping, even with an API key.", upwork, False),
    ("wellfound", "Wellfound", "Wellfound blocks robots from its job pages.", wellfound, True),
    ("yc", "YC Work at a Startup", "YC Work at a Startup has no public feed and refuses robots.", yc, True),
    ("contra", "Contra", "Contra has no public jobs feed and its terms are unknown.", contra, False),
    ("jooble", "Jooble", "Jooble's feed is only for websites, not for one person.", jooble, True),
)
ONLY_IN = {"naukri": "in"}
MAX_LINKS = 8


def cards(titles: list[str], locations: list[str], country_code: str = "") -> list[dict]:
    """One card per site. Each card holds up to MAX_LINKS saved searches (title by location)."""
    titles = [t.strip() for t in titles if t and t.strip()][:6] or ["Software Engineer"]
    locations = [l.strip() for l in locations if l and l.strip()][:4] or ["Remote"]
    out = []
    for key, name, why, build, by_place in SITES:
        if key in ONLY_IN and country_code != ONLY_IN[key]:
            continue                   # a site for one country appears only for a person in that country
        links, seen = [], set()
        for t in titles:
            for loc in (locations if by_place else [""]):
                url = build(t, loc, country_code) if key == "indeed" else build(t, loc)
                if url in seen:
                    continue
                seen.add(url)
                links.append({"label": f"{t}, {loc}" if loc else t, "url": url})
        out.append({"site": key, "name": name, "why": why + " Friday does not read it. Open a link and search by hand.",
                    "links": links[:MAX_LINKS]})
    return out
