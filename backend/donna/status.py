"""The status snapshot: `data/Friday/status.json`, written every minute by Friday's loop (also while she is paused).

It holds counts, the pause state, the caps and company names, so a terminal or a status board can see how she is doing.
It never holds an email address, a subject, mail text, a job post, an error text or a secret. Company names come from job posts,
so they are untrusted text: only letters, digits, spaces and `&.,'-` stay, anything that looks like a link goes, and at most 6
words are kept. The file says `"untrusted_names": true` so that a reader (a person or a model) does not obey a name (DJ10 L1).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from . import command
from . import sender as sender_mod

NAME_MAX = 60
NAME_WORDS = 6
NAMES_MAX = 20
_BAD = re.compile(r"[^\w &.,'’-]")
_LINK = re.compile(r"(?:https?:|www\.)\S*|[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\S*", re.I)


def clean_name(name) -> str:
    text = _BAD.sub(" ", _LINK.sub(" ", str(name or "")))
    return " ".join(text.split()[:NAME_WORDS])[:NAME_MAX]


def path(donna) -> Path:
    return Path(donna.cfg.work_dir) / "status.json"


def _names(names) -> list[str]:
    out: list[str] = []
    for n in names:
        n = clean_name(n)
        if n and n not in out:
            out.append(n)
    return out[:NAMES_MAX]


def snapshot(d) -> dict:
    now = d.clock()
    cap_day, cap_hour = sender_mod.caps(d.db)
    boards = d.dataset.counts()
    return {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "untrusted_names": True,
        "paused": d.is_paused(),
        "login_saved": d.login_status()["configured"],
        "profile_approved": d.learner.is_approved(),
        "cv_facts_ticked": d.cv.ticked_count(),
        "caps": {"per_day": cap_day, "per_hour": cap_hour, "sent_today": d.sender.sent_today(now),
                 "sent_last_hour": d.sender.sent_since(now - 3600)},
        "jobs": d.job_fetch.store.counts(),
        "scores": d.scorer.counts(),
        "boards": {k: v for k, v in boards["by_state"].items() if v},
        "companies_in_dataset": boards["companies"],
        "company_board": d.board.counts(),
        "funnel_today": {x["id"]: x["n"] for x in command.timeline(d)["steps"]},
        "budgets": {b["id"]: {"used": b["used"], "max": b["max"]} for b in command.budgets(d)},
        "writer_blocker": d.writer.blocker(),
        "drafts_waiting": d.writer.count("waiting"),
        "form_cards": len(d.router.form_cards()),
        "outbox": d.sender.counts(),
        "waiting_for_you": _names(d.sender.company_names(sender_mod.OPEN, NAMES_MAX)),
        "sent_recently": _names(d.sender.company_names(("sent",), 40, newest_sent_first=True)),
    }


def write(d) -> Path:
    """Write the snapshot through a random temp name (`O_CREAT | O_EXCL | O_NOFOLLOW`, mode 600), then rename."""
    target = path(d)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(snapshot(d), indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp = target.with_name(f".status-{secrets.token_hex(8)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target
