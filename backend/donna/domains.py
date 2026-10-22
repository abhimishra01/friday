"""Domain helpers shared by the router, the ledger and the sender. They live here so that the ledger and the sender do not import
routing to normalise a domain (DJ11 N7).

Three normalisers exist on purpose, and they must not be merged:
- `blocklist.normalise` keeps the full host, so a blocked domain also blocks its subdomains;
- `registrable` (here) gives the registrable domain, for matching a company's own site;
- `ledger.norm` calls `registrable`, so every agent counts "careers.acme.invalid" and "acme.invalid" as one company.
Merging them could weaken the blocklist's subdomain match.
"""
from __future__ import annotations

from urllib.parse import urlsplit

_SECOND_LEVEL = ("co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc")


def registrable(host: str) -> str:
    """example.com from careers.example.com; a two-letter country code after co, com, org and the like keeps three labels.
    No public-suffix list: good enough for a match test."""
    h = (host or "").lower().strip(".").split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    parts = [p for p in h.split(".") if p]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def host_of(url: str) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""
