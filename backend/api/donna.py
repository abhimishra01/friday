"""Friday's routes. Every route needs the page token (header X-Bots-Token, or ?token= for reads), the Host check and the Origin check.
Five actions need two steps (a one-time confirm token for exactly that action, target and text): connect Gmail, approve a send,
approve the profile, raise a cap, and resume after a pause. The first call returns {status: "needs_confirmation", confirm_token};
the second repeats the call with `confirm_token`. A token works once. The app password goes in and never comes out.

    GET  /api/Friday                      status: login (address only), pause, caps, counts
    PUT  /api/Friday/connect              {address, app_password}: save the login in the private file (two steps)
    POST /api/Friday/test                 log in to IMAP and SMTP, send nothing
    POST /api/Friday/pause                {paused: bool}: pausing is one step; resuming is two steps
    GET  /api/Friday/profile              what the learner found, with Keep and Remove
    POST /api/Friday/profile/learn        read the Sent folder (read only) and build a profile
    POST /api/Friday/profile/items/{id}   {keep: bool}
    POST /api/Friday/profile/examples/{id}  {state: "keep" | "remove"}
    POST /api/Friday/profile/approve      use this profile (two steps; the token fits the items and examples shown)
    GET  /api/Friday/cv                   the CV file's name and the facts list
    POST /api/Friday/cv/upload            {filename, data_base64}: a PDF (5 MB at most)
    POST /api/Friday/cv/text              {text}: the CV text, for a scanned PDF
    POST /api/Friday/cv/facts             {kind, text}: add a fact (it starts ticked)
    POST /api/Friday/cv/facts/tick        {ids: [..], ticked: bool}
    PATCH /api/Friday/cv/facts/{id}       {ticked: bool}
    DELETE /api/Friday/cv/facts/{id}
    GET  /api/Friday/dataset              the companies list and counts
    POST /api/Friday/dataset/import       {csv} or {sheet_url} or {seed: true}
    GET/POST /api/Friday/sheets           job-hunt sheets: list; add {url} or a CSV/XLSX file; /read-again, /mapping, DELETE
    GET  /api/Friday/boards               company job boards (?state=active|watch|moved|off)
    POST /api/Friday/boards/{id}          {state: "on" | "off" | "watch"}
    GET  /api/Friday/jobs                 live jobs with score and route (?limit=)
    GET  /api/Friday/cards                "apply via form" cards and "search by hand" cards
    POST /api/Friday/cards/{job_id}/applied
    GET  /api/Friday/outbox               waiting, undo, held and recent emails
    POST /api/Friday/outbox/queue         {draft_id}: a waiting draft joins the list
    POST /api/Friday/outbox/{id}/approve  {ok_other_agent?}: two steps; then a 10-second Undo
    POST /api/Friday/outbox/{id}/undo
    POST /api/Friday/outbox/{id}/reject
    POST /api/Friday/outbox/{id}/edit     {subject?, body?}: clears any approval
    POST /api/Friday/drafts/{id}/sounds_like_me   {yes: bool}
    POST /api/Friday/drafts/{id}/allow_word   {word}: the owner says a word the check refused is true (two steps)
    POST /api/Friday/drafts/{id}/rewrite  write a failed draft again (one model run)
    GET  /api/Friday/blocklist            {text}: the owner's blocklist, one rule a line (domain, name: Company, board: token)
    PUT  /api/Friday/blocklist            {text}: save it; removing a rule needs two steps
    GET  /api/Friday/events               the log
    GET  /api/Friday/settings             titles, locations, answers (and their questions), sign-off, time zone, caps
    PUT  /api/Friday/settings             any of the above; raising a cap needs two steps, lowering one does not
"""
from __future__ import annotations

import base64
import binascii
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..control.guard import Rejected
from ..donna import blocklist as blocklist_mod, home as home_mod, sender as sender_mod, status as status_mod
from ..donna import dataset as dataset_mod
from ..donna.fetch import DEFAULT_LOCATIONS
from ..donna.route import COMMON_QUESTIONS
from ..donna.score import TITLE_KEYWORDS

MAX_CSV = 5 * 1024 * 1024
MAX_PDF_B64 = 8 * 1024 * 1024
LIST_KEYS = {"titles": "prefs:titles", "locations": "prefs:locations", "feed_keywords": "feed_keywords"}
BOARD_STATES = {"on": "active", "active": "active", "off": "off", "watch": "watch"}
OPEN = sender_mod.OPEN


def _first_words(text: str, n: int = 80) -> str:
    return " ".join((text or "").split())[:n]


_OLD_NAME = re.compile(r"(?<![A-Za-z_])Donna(?![A-Za-z_])")


def friday(x):
    """Text saved before the rename still says Friday. A person sees Friday (DJ14LF). Applies to every string in a reply."""
    if isinstance(x, str):
        return _OLD_NAME.sub("Friday", x)
    if isinstance(x, list):
        return [friday(i) for i in x]
    if isinstance(x, dict):
        return {k: friday(v) for k, v in x.items()}
    return x


class FridayJSON(JSONResponse):
    """Every reply of these routes passes through `friday`, so no old "Friday" text reaches the page."""

    def render(self, content) -> bytes:
        return super().render(friday(content))


def build_donna_router(control, donna) -> APIRouter:
    async def clear_after_write(request: Request):
        """Any change (a POST, PUT, PATCH or DELETE) drops the cached page data, so the next read is fresh (DJ14LF)."""
        try:
            yield
        finally:
            if request.method != "GET":
                donna.cache_clear()

    r = APIRouter(prefix="/api/donna", dependencies=[Depends(clear_after_write)], default_response_class=FridayJSON)

    def guard(request: Request, token: str | None = None):
        try:
            control.guard.check(request.headers, token)
        except Rejected as e:
            h = request.headers
            control.log.write(phase="rejected", path=request.url.path, http_status=e.status, message=e.message,
                              origin=(h.get("origin") or "")[:100], host=(h.get("host") or "")[:100],
                              sec_fetch_site=(h.get("sec-fetch-site") or "")[:30])
            raise HTTPException(e.status, e.message)

    def confirm(d: dict, action: str, target_type: str, target_id, text: str, description: str, options: dict | None = None):
        """Without `confirm_token` the answer is a proposal with a one-time token that fits exactly this action, target, text and
        options. Returns None when the token was valid, else the proposal to send back."""
        target = {"type": target_type, "id": str(target_id)}
        tok = d.get("confirm_token")
        if not tok:
            t, _ = control.confirms.add(control.new_id(), action, target, text, description, {}, options)
            return {"status": "needs_confirmation", "confirm_token": t, "description": description,
                    "expires_in": int(control.confirms.ttl)}
        try:
            control.confirms.take(str(tok), action, target, text, options)
        except Rejected as e:
            raise HTTPException(e.status, e.message)
        return None

    async def body(request: Request) -> dict:
        try:
            d = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Send a JSON object.")
        if not isinstance(d, dict):
            raise HTTPException(400, "Send a JSON object.")
        return d

    def whole(value, low: int, high: int, what: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise HTTPException(400, f"{what} is a whole number from {low} to {high}.")
        return value

    def need_row(oid: int) -> dict:
        row = donna.sender.get(oid)
        if not row:
            raise HTTPException(404, "No email with that id.")
        return row

    # ---- status ----
    def status_view() -> dict:
        snap = status_mod.snapshot(donna)
        st = donna.status()
        return {**snap, "has_seed": bool(getattr(dataset_mod, "SEED_CSV", None)) and dataset_mod.SEED_CSV.is_file(), "login": donna.login_status(), "running": st["running"], "error": st["error"],
                "undo_seconds": sender_mod.UNDO_SECONDS,
                "caps": {**snap["caps"], "max_per_day": sender_mod.MAX_CAP_DAY, "max_per_hour": sender_mod.MAX_CAP_HOUR}}

    @r.get("")
    async def status(request: Request, token: str | None = None):
        guard(request, token)
        return await run_in_threadpool(donna.cached, "status", 15, status_view)

    @r.put("/connect")
    async def connect(request: Request):
        guard(request)
        d = await body(request)
        address = str(d.get("address") or "").strip().lower()
        need = confirm(d, "donna.connect", "gmail", address, "",
                       f"Use {address[:80]} as Friday's Gmail login. Friday will send job applications from this account, "
                       "each one only after you approve it.")
        if need:
            return need
        out = await run_in_threadpool(lambda: donna.save_login(str(d.get("address") or ""), str(d.get("app_password") or "")))
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The login was not saved.")
        return await run_in_threadpool(status_view)

    @r.post("/test")
    async def test(request: Request):
        guard(request)
        return await run_in_threadpool(donna.test_connection)

    @r.post("/pause")
    async def pause(request: Request):
        guard(request)
        d = await body(request)
        off = bool(d.get("paused"))
        if not off and donna.is_paused():
            need = confirm(d, "donna.resume", "switch", "donna", "",
                           "Switch Friday on. She will fetch jobs, run the model and send the emails you approve.")
            if need:
                return need
        await run_in_threadpool(donna.set_paused, off)
        return await run_in_threadpool(status_view)

    # ---- the profile ----
    @r.get("/profile")
    async def profile(request: Request, token: str | None = None):
        guard(request, token)
        return await run_in_threadpool(donna.learner.page_data)

    @r.post("/profile/learn")
    async def learn(request: Request):
        guard(request)
        return await run_in_threadpool(donna.learner.learn)

    @r.post("/profile/items/{item_id}")
    async def profile_item(item_id: str, request: Request):
        guard(request)
        d = await body(request)
        if not isinstance(d.get("keep"), bool):
            raise HTTPException(400, "Send keep: true or false.")
        if not await run_in_threadpool(donna.learner.set_keep, item_id, d["keep"]):
            raise HTTPException(404, "No such item.")
        return {"ok": True}

    @r.post("/profile/examples/{example_id}")
    async def profile_example(example_id: int, request: Request):
        guard(request)
        d = await body(request)
        if d.get("state") not in ("keep", "remove"):
            raise HTTPException(400, "Send state: keep or remove.")
        if not await run_in_threadpool(donna.learner.set_example, example_id, d["state"]):
            raise HTTPException(404, "No such example.")
        return {"ok": True}

    @r.post("/profile/approve")
    async def profile_approve(request: Request):
        guard(request)
        d = await body(request)
        page = await run_in_threadpool(donna.learner.page_data)
        items = [f"{g['group']}:{i['id']}:{i['text']}" for g in page.get("groups", []) for i in g["items"] if i["keep"]]
        examples = [f"example:{e['id']}:{e['subject']}" for e in page.get("examples", []) if e["state"] == "keep"]
        if page.get("state") == "none":
            raise HTTPException(400, "There is no profile yet. Let Friday read your Sent folder first.")
        if not items:
            raise HTTPException(400, "Keep at least one item first.")
        need = confirm(d, "donna.profile", "profile", "donna", "\n".join(items + examples),
                       f"Use this profile: {len(items)} kept item{'' if len(items) == 1 else 's'} and {len(examples)} example email{'' if len(examples) == 1 else 's'}. "
                       "Friday will write your applications in this style.")
        if need:
            return need
        out = await run_in_threadpool(donna.learner.use_profile)
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The profile was not approved.")
        return out

    # ---- the CV and the facts list ----
    @r.get("/cv")
    async def cv(request: Request, token: str | None = None):
        guard(request, token)
        return await run_in_threadpool(donna.cv.page_data)

    @r.post("/cv/upload")
    async def cv_upload(request: Request):
        guard(request)
        d = await body(request)
        raw = d.get("data_base64")
        if not isinstance(raw, str) or not raw:
            raise HTTPException(400, "Send the PDF as data_base64.")
        if len(raw) > MAX_PDF_B64:
            raise HTTPException(400, "The CV is larger than 5 MB. Save a smaller PDF and try again.")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "The file could not be read. Send it again.")
        out = await run_in_threadpool(donna.cv.upload, data, str(d.get("filename") or "cv.pdf"))
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The CV was not saved.")
        return out

    @r.post("/cv/text")
    async def cv_text(request: Request):
        guard(request)
        d = await body(request)
        out = await run_in_threadpool(donna.cv.from_text, str(d.get("text") or ""))
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The text was not saved.")
        return out

    @r.post("/cv/facts")
    async def cv_add(request: Request):
        guard(request)
        d = await body(request)
        out = await run_in_threadpool(donna.cv.add_fact, str(d.get("kind") or ""), str(d.get("text") or ""))
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The fact was not saved.")
        return await run_in_threadpool(donna.cv.page_data)

    @r.post("/cv/facts/tick")
    async def cv_tick(request: Request):
        guard(request)
        d = await body(request)
        ids = d.get("ids")
        if not isinstance(ids, list) or len(ids) > 500 or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids) \
                or not isinstance(d.get("ticked"), bool):
            raise HTTPException(400, "Send ids (a list of numbers) and ticked (true or false).")
        await run_in_threadpool(donna.cv.set_ticks, ids, d["ticked"])
        return await run_in_threadpool(donna.cv.page_data)

    @r.patch("/cv/facts/{fact_id}")
    async def cv_fact(fact_id: int, request: Request):
        guard(request)
        d = await body(request)
        if not isinstance(d.get("ticked"), bool):
            raise HTTPException(400, "Send ticked: true or false.")
        if not await run_in_threadpool(donna.cv.set_tick, fact_id, d["ticked"]):
            raise HTTPException(404, "No such fact.")
        return await run_in_threadpool(donna.cv.page_data)

    @r.post("/cv/facts/merge")
    async def cv_merge(request: Request):
        guard(request)
        n = await run_in_threadpool(donna.cv.merge_repeats)
        return {"ok": True, "removed": n, **await run_in_threadpool(donna.cv.page_data)}

    @r.delete("/cv/facts/{fact_id}")
    async def cv_remove(fact_id: int, request: Request):
        guard(request)
        if not await run_in_threadpool(donna.cv.remove_fact, fact_id):
            raise HTTPException(404, "No such fact.")
        return await run_in_threadpool(donna.cv.page_data)

    # ---- the companies list and the boards ----
    @r.get("/dataset")
    async def dataset(request: Request, token: str | None = None):
        guard(request, token)

        def view():
            return {**donna.dataset.counts(), "companies_list": donna.dataset.companies()}
        return await run_in_threadpool(view)

    @r.post("/dataset/import")
    async def dataset_import(request: Request):
        guard(request)
        d = await body(request)
        if d.get("seed") is True:
            out = await run_in_threadpool(donna.dataset.import_seed)
        elif isinstance(d.get("sheet_url"), str) and d["sheet_url"].strip():
            out = await run_in_threadpool(donna.dataset.import_sheet, d["sheet_url"].strip())
        elif isinstance(d.get("csv"), str) and d["csv"].strip():
            if len(d["csv"]) > MAX_CSV:
                raise HTTPException(400, "The file is larger than 5 MB.")
            out = await run_in_threadpool(donna.dataset.import_csv, d["csv"].encode("utf-8"))
        else:
            raise HTTPException(400, "Send csv (the file text), sheet_url, or seed: true.")
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The import failed.")
        return out

    @r.get("/command")
    async def command(request: Request, span: str = "today", token: str | None = None):
        guard(request, token)
        from ..donna import command as command_mod
        sp = "week" if span == "week" else "today"
        return friday(await run_in_threadpool(donna.cached, "command:" + sp, 20, lambda: command_mod.data(donna, sp)))

    # ---- search and apply intelligence (DJ14K) ----
    @r.get("/digest")
    async def digest(request: Request, token: str | None = None):
        guard(request, token)

        def view():
            return {**donna.intel.digest(), "outcomes": donna.intel.outcomes(), "connections": donna.intel.connection_count()}
        return friday(await run_in_threadpool(donna.cached, "digest", 60, view))

    @r.post("/connections")
    async def connections_upload(request: Request):
        guard(request)
        d = await body(request)
        raw = d.get("csv") if isinstance(d.get("csv"), str) else ""
        if not raw.strip():
            raise HTTPException(400, "Send csv (the text of your LinkedIn Connections.csv).")
        if len(raw) > MAX_CSV:
            raise HTTPException(400, "The file is larger than 5 MB.")
        out = await run_in_threadpool(donna.intel.import_connections, raw.encode("utf-8"))
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The file could not be read.")
        return out

    @r.delete("/connections")
    async def connections_remove(request: Request):
        guard(request)
        return await run_in_threadpool(donna.intel.clear_connections)

    # ---- the company board (DJ14I) ----
    @r.get("/board")
    async def board_view(request: Request, token: str | None = None):
        guard(request, token)
        return friday(await run_in_threadpool(donna.cached, "board", 20, donna.board.page_data))

    @r.get("/board/{cid}")
    async def board_company(cid: int, request: Request):
        guard(request)
        out = await run_in_threadpool(donna.board.company, cid)
        if not out:
            raise HTTPException(404, "No such company.")
        return friday(out)

    @r.post("/board/{cid}/status")
    async def board_status(cid: int, request: Request):
        guard(request)
        d = await body(request)
        note = d.get("note") if isinstance(d.get("note"), str) else None
        out = await run_in_threadpool(donna.board.set_status, cid, str(d.get("status") or ""), note)
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The status could not be set.")
        return out

    @r.post("/board/{cid}/note")
    async def board_note(cid: int, request: Request):
        guard(request)
        d = await body(request)
        out = await run_in_threadpool(donna.board.set_note, cid, str(d.get("note") or ""))
        if not out.get("ok"):
            raise HTTPException(404, out.get("error") or "No such company.")
        return out

    @r.post("/board/{cid}/dismiss")
    async def board_dismiss(cid: int, request: Request):
        guard(request)
        out = await run_in_threadpool(donna.board.dismiss_rejection, cid)
        if not out.get("ok"):
            raise HTTPException(404, out.get("error") or "No such company.")
        return out

    @r.post("/board/replies")
    async def board_replies(request: Request):
        guard(request)
        out = await run_in_threadpool(donna.board.read_replies)
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "Replies could not be read.")
        return out

    # ---- job-hunt sheets (DJ14H): read once, kept in Donna's database ----
    @r.get("/sheets")
    async def sheets(request: Request, token: str | None = None):
        guard(request, token)
        return friday(await run_in_threadpool(donna.sheets.page_data))

    @r.post("/sheets")
    async def sheets_add(request: Request):
        guard(request)
        d = await body(request)
        if isinstance(d.get("url"), str) and d["url"].strip():
            out = await run_in_threadpool(donna.sheets.add_link, d["url"].strip())
        elif isinstance(d.get("data_base64"), str) and d["data_base64"]:
            import base64
            import binascii
            if len(d["data_base64"]) > MAX_PDF_B64:
                raise HTTPException(400, "The file is larger than 5 MB.")
            try:
                raw = base64.b64decode(d["data_base64"], validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(400, "The file could not be read.")
            out = await run_in_threadpool(donna.sheets.add_file, str(d.get("filename") or ""), raw)
        elif isinstance(d.get("csv"), str) and d["csv"].strip():
            if len(d["csv"]) > MAX_CSV:
                raise HTTPException(400, "The file is larger than 5 MB.")
            out = await run_in_threadpool(donna.sheets.add_file, str(d.get("filename") or "Pasted sheet.csv"), d["csv"].encode("utf-8"))
        else:
            raise HTTPException(400, "Send url (a Google Sheets link) or a CSV or XLSX file.")
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The sheet could not be added.")
        return out

    @r.post("/sheets/{sheet_id}/read-again")
    async def sheets_again(sheet_id: int, request: Request):
        guard(request)
        out = await run_in_threadpool(donna.sheets.read_again, sheet_id)
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The sheet could not be read.")
        return out

    @r.post("/sheets/{sheet_id}/mapping")
    async def sheets_mapping(sheet_id: int, request: Request):
        guard(request)
        d = await body(request)
        mapping = d.get("mapping") if isinstance(d.get("mapping"), dict) else {}
        out = await run_in_threadpool(donna.sheets.use_mapping, sheet_id, str(d.get("tab") or ""), mapping)
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The columns could not be used.")
        return out

    @r.delete("/sheets/{sheet_id}")
    async def sheets_remove(sheet_id: int, request: Request):
        guard(request)
        if not await run_in_threadpool(donna.sheets.remove, sheet_id):
            raise HTTPException(404, "No such sheet.")
        return {"ok": True}

    @r.get("/boards")
    async def boards(request: Request, state: str = "", token: str | None = None):
        guard(request, token)
        if state and state not in ("active", "watch", "moved", "off"):
            raise HTTPException(400, "Choose a state: active, watch, moved or off.")
        return await run_in_threadpool(donna.dataset.boards, state or None)

    @r.post("/boards/{board_id}")
    async def board(board_id: int, request: Request):
        guard(request)
        d = await body(request)
        state = BOARD_STATES.get(d.get("state")) if isinstance(d.get("state"), str) else None
        if not state:
            raise HTTPException(400, "Send state: on, off or watch.")
        if not await run_in_threadpool(donna.dataset.set_board_state, board_id, state):
            raise HTTPException(404, "No such board.")
        return {"ok": True, "state": state}

    # ---- jobs and cards ----
    @r.get("/jobs")
    async def jobs(request: Request, limit: int = 200, token: str | None = None):
        guard(request, token)

        def view():
            scores, routes = donna.scorer.table(), donna.router.kinds()
            out = []
            for j in donna.job_fetch.store.live():          # no post text: the page shows facts and links
                s = scores.get(j["id"]) or {}
                out.append({"id": j["id"], "company": j["company"], "title": j["title"], "location": j["location"], "remote": bool(j["remote"]),
                            "posted": j["posted"], "job_url": j["job_url"], "apply_url": j["apply_url"], "sources": j["sources"],
                            "first_seen": j["first_seen"], "score_state": s.get("state", ""), "score": s.get("total"),
                            "why": s.get("why", ""), "gap": s.get("gap", ""), "reason": s.get("reason", ""), "route": routes.get(j["id"], "")})
            out.sort(key=lambda x: (-(x["score"] or -1), x["id"]))
            return out[:max(1, min(1000, limit))]
        return await run_in_threadpool(view)

    @r.get("/cards")
    async def cards(request: Request, token: str | None = None):
        guard(request, token)

        def view():
            return {"forms": donna.router.form_cards(), "search": donna.job_fetch.search_cards()}
        return await run_in_threadpool(view)

    @r.post("/cards/{job_id}/applied")
    async def card_applied(job_id: int, request: Request):
        guard(request)
        if not await run_in_threadpool(donna.router.mark_applied, job_id):
            raise HTTPException(404, "No such job.")
        return {"ok": True}

    # ---- the outbox ----
    def outbox_view() -> dict:
        now = donna.sender.now()
        # Open rows, and only the 30 newest closed rows: not the whole history, one row at a time (DJ14LG).
        recent_ids = [r["id"] for r in donna.db.query(
            "SELECT id FROM outbox WHERE state NOT IN (" + ",".join("?" * len(OPEN)) + ") ORDER BY id DESC LIMIT 30", tuple(OPEN))]
        rows = donna.sender.list(*OPEN) + [donna.sender.get(i) for i in reversed(recent_ids)]
        for it in rows:
            it["undo_left"] = max(0.0, round((it["undo_until"] or 0) - now, 1)) if it["state"] == "undo" else 0
        scores, cv_name = donna.scorer.table(), (donna.cv.meta() or {}).get("filename") or ""
        open_rows = [i for i in rows if i["state"] in OPEN]
        for it in open_rows:                                    # why this role fits, for the draft card (row 36)
            sc = scores.get(it["job_id"]) or {}
            it["why"], it["gap"], it["score"] = sc.get("why", ""), sc.get("gap", ""), sc.get("total")
        recent = [i for i in rows if i["state"] not in OPEN][-30:]
        failed = [{"id": d["id"], "kind": d["kind"], "job_id": d["job_id"], "company": d["company"], "title": d["title"],
                   "unknown": d["unknown"], "problems": d["problems"], "reason": d["reason"]} for d in donna.writer.failed()]
        return {"waiting": open_rows, "recent": list(reversed(recent)), "failed_drafts": failed, "cv_file": cv_name, "status": donna.sender.status(),
                "sounds_like_me": donna.writer.sounds_like_me_counts()}

    @r.get("/outbox")
    async def outbox(request: Request, token: str | None = None):
        guard(request, token)
        return await run_in_threadpool(outbox_view)

    @r.post("/outbox/queue")
    async def outbox_queue(request: Request):
        guard(request)
        d = await body(request)
        did = whole(d.get("draft_id"), 1, 2**31, "The draft id")
        return await run_in_threadpool(donna.sender.queue, did)

    @r.post("/outbox/{oid}/approve")
    async def approve(oid: int, request: Request):
        guard(request)
        d = await body(request)
        it = await run_in_threadpool(need_row, oid)
        if it["state"] not in sender_mod.APPROVABLE:          # a wrong state is no reason to ask the owner to confirm
            raise HTTPException(409, "This email is not waiting for approval.")
        ok_other = d.get("ok_other_agent") is True
        meta = await run_in_threadpool(donna.cv.meta) or {}
        sender = (await run_in_threadpool(donna.login_status))["address"]
        # The token binds the stored subject and text and the tick for another agent: an edit between the two steps voids it.
        need = confirm(d, "donna.approve", "outbox", oid, f"{it['subject']}\n{it['body']}",
                       f"Send to {it['company'] or it['domain']} ({it['to_addr']}) from Friday's Gmail{' (' + sender + ')' if sender else ''}. "
                       f"Subject: {it['subject'][:120]}. First words: {_first_words(it['body'])}. "
                       f"File: {meta.get('filename') or 'your CV'}. You can undo for {sender_mod.UNDO_SECONDS} seconds.",
                       {"ok_other_agent": ok_other})
        if need:
            return need
        out = await run_in_threadpool(lambda: donna.sender.approve(oid, ok_other))
        if out.get("status") == "undo":
            donna.nudge(sender_mod.UNDO_SECONDS + 1)          # send as soon as the Undo is over, not at the next minute
        return out

    @r.post("/outbox/{oid}/undo")
    async def undo(oid: int, request: Request):
        guard(request)
        await run_in_threadpool(need_row, oid)
        return await run_in_threadpool(donna.sender.undo, oid)

    @r.post("/outbox/{oid}/reject")
    async def reject(oid: int, request: Request):
        guard(request)
        row = await run_in_threadpool(need_row, oid)
        if row["state"] not in sender_mod.REJECTABLE:
            raise HTTPException(409, "This email cannot be rejected now.")
        return await run_in_threadpool(donna.sender.reject, oid)

    @r.post("/outbox/{oid}/edit")
    async def edit(oid: int, request: Request):
        guard(request)
        d = await body(request)
        await run_in_threadpool(need_row, oid)
        subject = d.get("subject") if isinstance(d.get("subject"), str) else None
        text = d.get("body") if isinstance(d.get("body"), str) else None
        return await run_in_threadpool(lambda: donna.sender.edit(oid, subject, text))

    @r.post("/drafts/{draft_id}/sounds_like_me")
    async def sounds(draft_id: int, request: Request):
        guard(request)
        d = await body(request)
        if not isinstance(d.get("yes"), bool):
            raise HTTPException(400, "Send yes: true or false.")
        if not await run_in_threadpool(donna.writer.mark_sounds_like_me, draft_id, d["yes"]):
            raise HTTPException(404, "No such draft.")
        return await run_in_threadpool(donna.writer.sounds_like_me_counts)

    # ---- the claims check: allow a word, write again ----
    @r.post("/drafts/{draft_id}/allow_word")
    async def allow_word(draft_id: int, request: Request):
        guard(request)
        d = await body(request)
        word = " ".join(str(d.get("word") or "").split())[:60]
        draft = await run_in_threadpool(donna.writer.get, draft_id)
        if not draft or draft["state"] != "failed":
            raise HTTPException(404, "No failed draft with that id.")
        if not word or word not in draft["unknown"]:
            raise HTTPException(400, "Send one of the words the check refused.")
        need = confirm(d, "donna.allow_word", "draft", draft_id, word,
                       f"Say that \"{word}\" is true about you. Friday may then write it in your applications. Only allow what you can stand behind.")
        if need:
            return need
        await run_in_threadpool(donna.writer.allow_word, word)
        return {"ok": True, "word": word}

    @r.post("/drafts/{draft_id}/rewrite")
    async def rewrite(draft_id: int, request: Request):
        guard(request)

        def work():
            draft = donna.writer.get(draft_id)
            if not draft or draft["job_id"] is None or draft["id"] not in {x["id"] for x in donna.writer.failed()}:
                raise HTTPException(404, "No failed draft for a job with that id.")
            out = donna.writer.write(draft["job_id"])
            donna.queue_drafts()
            return {"state": out.get("state"), "reason": out.get("reason", "")}
        return await run_in_threadpool(work)

    # ---- the owner's blocklist (PLAN-FINAL row 42) ----
    @r.get("/blocklist")
    async def get_blocklist(request: Request, token: str | None = None):
        guard(request, token)
        return {"text": await run_in_threadpool(lambda: donna.blocklist.text())}

    @r.put("/blocklist")
    async def put_blocklist(request: Request):
        guard(request)
        d = await body(request)
        text = d.get("text")
        if not isinstance(text, str):
            raise HTTPException(400, "Send text: one rule a line.")
        new, bad = blocklist_mod.parse_rules(text)
        if bad:
            raise HTTPException(400, "These lines are not rules: " + ", ".join(bad[:5]) + ". Write one rule a line: a domain "
                                "(example.com), name: Company Name, or board: token.")
        old = await run_in_threadpool(lambda: donna.blocklist.rules())
        gone = [x for x in (*old.domains, *old.names, *old.boards) if x not in (*new.domains, *new.names, *new.boards)]
        if gone:                                         # taking a company off the list lowers a guard: two steps
            need = confirm(d, "donna.unblock", "blocklist", "donna", "\n".join(gone),
                           f"Take {len(gone)} rule(s) off your blocklist. Friday may then write to those companies.")
            if need:
                return need
        out = await run_in_threadpool(donna.blocklist.save, text)
        if not out.get("ok"):
            raise HTTPException(400, out.get("error") or "The blocklist was not saved.")
        await run_in_threadpool(donna.db.event, "blocklist", f"Blocklist saved: {len(new.domains)} domains, {len(new.names)} names, {len(new.boards)} boards", donna.clock)
        return {"ok": True, "text": await run_in_threadpool(lambda: donna.blocklist.text())}

    @r.get("/events")
    async def events(request: Request, token: str | None = None):
        guard(request, token)
        return friday(await run_in_threadpool(donna.db.events, 100))

    # ---- settings ----
    def settings_view() -> dict:
        def lst(key, default):
            v = donna.db.get_json(key, None, list)
            return [str(x) for x in v] if v else default
        answers = donna.db.get_json("prefs:answers", {}, dict)
        cap_day, cap_hour = sender_mod.caps(donna.db)
        return {"titles": lst("prefs:titles", list(TITLE_KEYWORDS)), "locations": lst("prefs:locations", list(DEFAULT_LOCATIONS)),
                "feed_keywords": lst("feed_keywords", []), "answers": answers,
                "answer_questions": [{"key": k, "question": q} for k, q in COMMON_QUESTIONS],
                "home": {**json.loads(home_mod.load(donna.db).to_json()), "tz_label": home_mod.load(donna.db).tz_label},
                "salary_floor": donna.db.get("prefs:salary_floor", ""), "work_type": donna.db.get("prefs:work_type", ""),
                "sign_off": donna.db.get("prefs:sign_off", ""), "recipient_tz": donna.db.get("prefs:recipient_tz", ""),
                "learner_months": donna.learner.months(),
                "caps": {"per_day": cap_day, "per_hour": cap_hour, "max_per_day": sender_mod.MAX_CAP_DAY,
                         "max_per_hour": sender_mod.MAX_CAP_HOUR, "default_per_day": sender_mod.CAP_DAY,
                         "default_per_hour": sender_mod.CAP_HOUR}}

    @r.get("/settings")
    async def settings(request: Request, token: str | None = None):
        guard(request, token)
        return await run_in_threadpool(settings_view)

    @r.put("/settings")
    async def put_settings(request: Request):
        guard(request)
        d = await body(request)
        # Check every field first, so a bad field saves nothing.
        save: dict[str, str] = {}
        for name, key in LIST_KEYS.items():
            if name in d:
                v = d[name]
                if not isinstance(v, list) or len(v) > 30 or not all(isinstance(x, str) and 0 < len(x.strip()) <= 80 for x in v):
                    raise HTTPException(400, f"{name} is a list of up to 30 short texts.")
                save[key] = json.dumps([x.strip() for x in v])
        if "answers" in d:
            v = d["answers"]
            if not isinstance(v, dict) or len(v) > 40 or not all(isinstance(k, str) and isinstance(x, str) and len(x) <= 300 for k, x in v.items()):
                raise HTTPException(400, "answers is a set of short texts (300 letters at most each).")
            save["prefs:answers"] = json.dumps(v)
        if "sign_off" in d:
            v = d["sign_off"]
            if not isinstance(v, str) or len(v) > 200:
                raise HTTPException(400, "The sign-off is a text of 200 letters at most.")
            save["prefs:sign_off"] = v.strip()
        if "home" in d:
            v = d["home"]
            if not isinstance(v, dict):
                raise HTTPException(400, "home is a set of country, places, time zone.")
            for k in ("places", "regions"):
                if k in v and (not isinstance(v[k], list) or len(v[k]) > 60 or not all(isinstance(x, str) and len(x) <= 60 for x in v[k])):
                    raise HTTPException(400, f"home.{k} is a list of short texts.")
            if any(k in v and not isinstance(v[k], str) for k in ("country", "tz", "country_code")):
                raise HTTPException(400, "home.country, home.tz and home.country_code are texts.")
            tz = str(v.get("tz") or "").strip()
            if tz:
                try:
                    ZoneInfo(tz)
                except (ZoneInfoNotFoundError, ValueError, OSError):
                    raise HTTPException(400, "Unknown time zone. Use a name such as America/Sao_Paulo or Europe/London.")
            h = home_mod.from_dict(v)
            code = h.country_code
            if code and not code.isalpha():
                raise HTTPException(400, "The country code is two letters.")
            if h.country and h.country.lower() not in h.places:       # the country name always counts as a place
                h = home_mod.from_dict({**json.loads(h.to_json()), "places": [*h.places, h.country]})
            save["prefs:home"] = h.to_json()
        for name, key, limit in (("salary_floor", "prefs:salary_floor", 60), ("work_type", "prefs:work_type", 20)):
            if name in d:
                v = d[name]
                if not isinstance(v, str) or len(v) > limit or (name == "work_type" and v not in ("", "remote", "hybrid", "onsite", "any")):
                    raise HTTPException(400, f"{name} is a short text" + (" (remote, hybrid, onsite or any)." if name == "work_type" else "."))
                save[key] = v.strip()
        if "recipient_tz" in d:
            v = d["recipient_tz"]
            if not isinstance(v, str):
                raise HTTPException(400, "The time zone is a name such as America/New_York.")
            if v.strip():
                try:
                    ZoneInfo(v.strip())
                except (ZoneInfoNotFoundError, ValueError, OSError):
                    raise HTTPException(400, "Unknown time zone. Use a name such as Europe/London or America/New_York.")
            save["prefs:recipient_tz"] = v.strip()
        months = whole(d["learner_months"], 1, 60, "The number of months") if "learner_months" in d else None
        old_day, old_hour = sender_mod.caps(donna.db)
        new_day = whole(d["cap_per_day"], 0, sender_mod.MAX_CAP_DAY, "The daily limit") if "cap_per_day" in d else old_day
        new_hour = whole(d["cap_per_hour"], 0, sender_mod.MAX_CAP_HOUR, "The hourly limit") if "cap_per_hour" in d else old_hour
        if new_day > old_day or new_hour > old_hour:
            need = confirm(d, "donna.raise_cap", "caps", "donna", json.dumps({"per_day": new_day, "per_hour": new_hour}),
                           f"Raise Friday's limits to {new_day} a day and {new_hour} an hour (now {old_day} and {old_hour}). "
                           "More emails can go out under your name.")
            if need:
                return need
        if new_day != old_day:
            save["cap:day"] = str(new_day)
        if new_hour != old_hour:
            save["cap:hour"] = str(new_hour)
        for key, value in save.items():
            await run_in_threadpool(donna.db.put, key, value)
        if months is not None:
            await run_in_threadpool(donna.learner.set_months, months)
        if save or months is not None:
            await run_in_threadpool(donna.db.event, "settings", "Settings changed: " + ", ".join(sorted(k.split(":")[-1] for k in save)), donna.clock)
        return await run_in_threadpool(settings_view)

    return r
