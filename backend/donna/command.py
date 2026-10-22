"""Friday's command centre data (DJ14J): one read for the page top. Counts and plain sentences only; it is also what status.json draws on.

Everything here comes from Friday's own records (no new table): the day's events, the model-run counters, the outbox, the scores, the setup
steps. A blocker that stops her (paused, no profile, no ticked facts, no sign-off, a budget used up) shows as a task for the owner.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from . import sender as sender_mod
from .score import DRAFT_AT, MAYBE_AT, SCORE_RUNS_A_DAY
from .writer import WRITE_RUNS_A_DAY

NO_FIT = ("filtered", "dropped_money", "flagged")
RESUMES = "00:00"
LOG_MAX = 8


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def timeline(d, span: str = "today") -> dict:
    """Where the jobs are: counts per step for the jobs first seen today (or in the last 7 days)."""
    today = d.today()
    first = today if span == "today" else (date.fromisoformat(today) - timedelta(days=6)).isoformat()
    jobs = [r["id"] for r in d.db.query("SELECT id FROM jobs WHERE first_seen >= ?", (first,))]
    ids = set(jobs)
    scores = {r["job_id"]: r for r in d.db.query("SELECT job_id, state, total FROM job_scores")}
    fit = {j for j in ids if j in scores and scores[j]["state"] not in NO_FIT}
    s70 = {j for j in fit if scores[j]["total"] is not None and scores[j]["total"] >= DRAFT_AT}
    near = {j for j in fit if scores[j]["total"] is not None and MAYBE_AT + 10 <= scores[j]["total"] < DRAFT_AT}
    drafted = {r["job_id"] for r in d.db.query("SELECT job_id FROM drafts WHERE job_id IS NOT NULL")} & s70
    stopped = {r["job_id"] for r in d.db.query("SELECT job_id FROM drafts WHERE state='failed' AND job_id IS NOT NULL")} & s70
    ob = d.db.query("SELECT job_id, state FROM outbox WHERE job_id IS NOT NULL")
    ready = {r["job_id"] for r in ob if r["state"] in ("waiting", "held", "undo", "queued")} & s70
    sent = {r["job_id"] for r in ob if r["state"] == "sent"} & s70
    unscored = len([j for j in ids if j not in scores])
    steps = [("found", "Found", "New on your boards", len(ids)), ("fit", "Fit you", "Right role, place and level", len(fit)),
             ("s70", "Scored 70+", "Strong match to your CV", len(s70)), ("drafted", "Drafted", "A note is written", len(drafted)),
             ("stopped", "Stopped", "Needs your yes or no", len(stopped)), ("ready", "Ready", "Waiting for your Approve", len(ready)),
             ("sent", "Sent", "Nothing goes without you", len(sent))]
    why = ""
    if ids:
        gone = len(ids) - len(fit) - unscored
        why = f"{gone} of {len(ids)} jobs did not fit your filters." if gone > 0 else ""
        if unscored:
            why = (why + " " if why else "") + f"{unscored} wait to be scored."
        if near:
            why += f" {len(near)} near miss{'es' if len(near) != 1 else ''} (scored {MAYBE_AT + 10} to {DRAFT_AT - 1}) kept for a look."
    return {"span": span, "steps": [{"id": i, "name": n, "why": w, "n": c} for i, n, w, c in steps], "say": why.strip(), "near_misses": len(near)}


def budgets(d) -> list[dict]:
    cap_day, _h = sender_mod.caps(d.db)
    sent = d.sender.sent_today(d.clock())
    rows = [("score", "Scoring runs", d.scorer.runs_today(), SCORE_RUNS_A_DAY), ("write", "Drafting runs", d.writer.runs_today(), WRITE_RUNS_A_DAY),
            ("mail", "Emails sent", sent, cap_day)]
    return [{"id": i, "name": n, "used": u, "max": m, "out": m > 0 and u >= m,
             "note": f"Used up. She starts again at {RESUMES}." if m > 0 and u >= m else ""} for i, n, u, m in rows]


def setup(d) -> list[dict]:
    login = d.login_status()["configured"]
    cv = d.cv.meta()
    ticked = d.cv.ticked_count()
    return [{"id": "gmail", "name": "Friday's Gmail", "done": login, "go": "settings"},
            {"id": "cv", "name": "Your CV", "done": ticked > 0 or bool(cv), "go": "cv"},
            {"id": "facts", "name": "Facts she may use", "done": ticked > 0, "go": "cv"},
            {"id": "style", "name": "Your writing style", "done": d.learner.is_approved(), "go": "profile"},
            {"id": "signoff", "name": "How you end emails", "done": bool(d.db.get("prefs:sign_off", "").strip()), "go": "settings"}]


def log(d) -> list[dict]:
    day0 = time.mktime(time.strptime(d.today(), "%Y-%m-%d"))
    return [{"time": _hm(e["ts"]), "text": e["text"]} for e in d.db.query("SELECT ts, kind, text FROM events WHERE ts >= ? AND kind != 'pause' ORDER BY id DESC LIMIT ?", (day0, LOG_MAX))]


def needs(d, blocker: str, st: dict) -> list[dict]:
    """At most 3 tasks, one button each, with the time it takes."""
    out = []
    ready = d.db.query("SELECT COUNT(*) AS n FROM outbox WHERE state IN ('waiting','held')")[0]["n"]
    failed = len(d.writer.failed())
    if blocker and not d.is_paused():
        go = "profile" if "profile" in blocker else "cv" if "CV" in blocker else "settings" if "sign-off" in blocker else "settings"
        out.append({"id": "blocker", "title": blocker, "why": "She cannot write until this is done.", "btn": "Fix it", "go": go, "mins": 1})
    if ready:
        out.append({"id": "approve", "title": f"Approve {ready} ready draft{'s' if ready != 1 else ''}", "why": "Each one shows why it fits.", "btn": "Review",
                    "go": "wait", "mins": max(1, ready // 2 + 1)})
    if failed:
        out.append({"id": "stopped", "title": f"{failed} draft{'s' if failed != 1 else ''} stopped by the check", "why": "They name facts Friday cannot back up. Tick the true ones.",
                    "btn": "Check words", "go": "wait", "mins": 1})
    rej = d.db.query("SELECT COUNT(*) AS n FROM company_status WHERE unconfirmed=1")[0]["n"]
    if rej:
        out.append({"id": "reject", "title": f"Confirm {rej} possible rejection{'s' if rej != 1 else ''}", "why": "A reply has rejection wording.", "btn": "Open", "go": "companies", "mins": 1})
    forms = len(d.router.form_cards())
    if forms:
        out.append({"id": "forms", "title": f"{forms} job{'s' if forms != 1 else ''} need an application form", "why": "Friday cannot email these. She prepared the answers.", "btn": "Open", "go": "forms", "mins": 2})
    return out[:3]


def data(d, span: str = "today") -> dict:
    st_paused = d.is_paused()
    login = d.login_status()["configured"]
    blocker = d.writer.blocker()
    bud = budgets(d)
    out_b = [b for b in bud if b["out"]]
    todo = [s for s in setup(d) if not s["done"]]
    if st_paused:
        head, sub, tone = "Friday is paused.", "She fetches nothing and sends nothing. Switch her on to start.", "stop"
    elif not login:
        head, sub, tone = "Friday is not started.", "Connect her Gmail first.", "stop"
    elif out_b:
        head, sub, tone = f"Stopped: {out_b[0]['name'].lower()} used up.", f"She starts again at {RESUMES}, or you can raise the limit in Settings.", "stop"
    elif blocker:
        head, sub, tone = "Waiting for you.", blocker, "you"
    else:
        head, sub, tone = "Friday is working.", "She reads boards once a day, scores, and writes drafts. She sends only what you approve.", "work"
    tl = timeline(d, span)
    n = needs(d, blocker, {})
    board = d.cached("board", 20, d.board.page_data)
    needs_companies = sum(1 for c in board["companies"] if c["needs"])
    return {"updated": d.clock(), "state": {"head": head, "sub": sub, "tone": tone, "paused": st_paused}, "log": log(d), "needs": n, "needs_companies": needs_companies,
            "needs_mins": sum(x["mins"] for x in n), "budgets": bud, "setup": setup(d), "setup_left": len(todo), "timeline": tl,
            "health": {"ok": login and not st_paused, "text": "Gmail login saved. CV and job boards read." if login else "Gmail is not connected."}}
