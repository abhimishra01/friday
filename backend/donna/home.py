"""Where the person lives and works from. Every place or time-zone rule in Friday reads this and nothing else (DJ50).

Nothing is assumed: a new install has an empty home, and the first-run page asks for it. The values live in the database under
`prefs:home`. A database that already holds settings from an older version gets its old values once, from `legacy.py`
(a file the shipped copy does not contain), so an existing install keeps working exactly as before.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HOME_KEY = "prefs:home"
SETUP_KEY = "setup_version"
_OFFSET = r"([+\-−–]\s*\d{1,2}(?:[:.]\d{2})?)"
_UTC_ONE = re.compile(r"(?:utc|gmt)\s*" + _OFFSET, re.I)
_TZ_RANGE = re.compile(r"(?:utc|gmt)\s*" + _OFFSET + r"\s*(?:to|-|–|—|and|through|until|/)\s*(?:utc|gmt)?\s*" + _OFFSET, re.I)
_US_NAMES = {"united states", "usa", "us", "u.s.", "america"}


def offset_hours(s: str) -> float:
    s = s.replace(" ", "").replace("−", "-").replace("–", "-")
    sign = -1 if s.startswith("-") else 1
    h, _, m = s[1:].replace(".", ":").partition(":")
    return sign * (int(h) + (int(m) / 60 if m else 0))


@dataclass(frozen=True)
class Home:
    country: str = ""
    places: tuple = ()          # lower-case place words: the country, its cities, other names for them
    tz: str = ""                # an IANA name such as America/Sao_Paulo
    regions: tuple = ()         # extra words for an open region the person can work from (for example "latam")
    country_code: str = ""      # two letters, optional (a job board that filters by country)

    @property
    def empty(self) -> bool:
        return not (self.places or self.tz or self.country)

    @property
    def utc_offset(self) -> float | None:
        if not self.tz:
            return None
        try:
            off = datetime.now(ZoneInfo(self.tz)).utcoffset()
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
        return off.total_seconds() / 3600 if off else None

    @property
    def tz_label(self) -> str:
        o = self.utc_offset
        if not self.tz or o is None:
            return ""
        h = int(abs(o)); m = int(round((abs(o) - h) * 60))
        return f"{self.tz} (UTC{'-' if o < 0 else '+'}{h}{f':{m:02d}' if m else ''})"

    @property
    def name(self) -> str:
        """The country as a word in a sentence; 'your country' when none is set."""
        return self.country or "your country"

    @property
    def is_us(self) -> bool:
        return bool(_US_NAMES & {p.lower() for p in self.places} | ({self.country.lower()} & _US_NAMES))

    def place_re(self):
        words = [p for p in (*self.places, self.country.lower()) if p]
        return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in sorted(set(words), key=len, reverse=True)) + r")\b", re.I) if words else None

    def region_re(self):
        return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in self.regions) + r")\b", re.I) if self.regions else None

    def in_place(self, text: str) -> bool:
        rx = self.place_re()
        return bool(rx and rx.search(text or ""))

    def in_region(self, text: str) -> bool:
        rx = self.region_re()
        return bool(rx and rx.search(text or ""))

    def covers_time(self, text: str) -> bool:
        """The text names a UTC range that holds this person's offset, or names that exact offset."""
        o = self.utc_offset
        if o is None:
            return False
        for m in _TZ_RANGE.finditer(text or ""):
            lo, hi = sorted((offset_hours(m.group(1)), offset_hours(m.group(2))))
            if lo <= o <= hi:
                return True
        return any(abs(offset_hours(m.group(1)) - o) < 0.01 for m in _UTC_ONE.finditer(text or ""))

    def to_json(self) -> str:
        return json.dumps({"country": self.country, "places": list(self.places), "tz": self.tz, "regions": list(self.regions),
                           "country_code": self.country_code})


def _list(v) -> tuple:
    return tuple(str(x).strip().lower() for x in (v or []) if str(x).strip())


def from_dict(d: dict) -> Home:
    d = d if isinstance(d, dict) else {}
    return Home(str(d.get("country") or "").strip()[:80], _list(d.get("places")), str(d.get("tz") or "").strip(),
                _list(d.get("regions")), str(d.get("country_code") or "").strip().lower()[:2])


def load(db) -> Home:
    return from_dict(db.get_json(HOME_KEY, {}, dict))


def save(db, h: Home) -> None:
    db.put(HOME_KEY, h.to_json())


def first_open(db) -> None:
    """Once per database. A database with settings or jobs from an older version gets the old built-in values (legacy.py, which only
    the first owner's copy has); a new one gets nothing."""
    if db.get(SETUP_KEY):
        return
    used = db.query("SELECT 1 FROM settings LIMIT 1") or db.query("SELECT 1 FROM jobs LIMIT 1")
    if used:
        try:
            from . import legacy
        except ImportError:
            legacy = None
        if legacy:
            legacy.apply(db)
    db.put(SETUP_KEY, "1")
