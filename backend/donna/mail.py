"""Friday's thin wrapper on the mail module's `assistant/mailio.py` (which stays unchanged and does not read the mail module's settings).

What it adds: Friday's own login (from her private file), Friday's own pause check, and read-only reading helpers.
- IMAP is read-only: `mailio.fetch_new` opens the folder with readonly=True and fetches with BODY.PEEK[], so Friday never marks,
  moves or deletes the owner's mail.
- This file never sends. `Sender.evaluate` in `backend/Friday/sender.py` is the one gate that stops a send (pause, login, every rule).
"""
from __future__ import annotations

import re
from datetime import date
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Callable

from ..assistant import mailio
from . import config


HEADER_FIELDS = "SUBJECT FROM TO CC BCC DATE MESSAGE-ID IN-REPLY-TO"
HEADER_BATCH = 100              # headers per FETCH
HEADER_CAP = 5000               # newest headers looked at in one run
BODY_PARTIAL = 96 * 1024        # the first bytes of a chosen mail: the text comes first, a CV attachment after it
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_date(d: date) -> str:
    """01-Oct-2026. Built by hand, so the month name never depends on the computer's language."""
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def parse_header_bytes(uid: int, raw: bytes) -> dict:
    m = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    name, addr = parseaddr(str(m["From"] or ""))
    try:
        ts = parsedate_to_datetime(str(m["Date"])).timestamp()
    except Exception:  # noqa: BLE001
        ts = None
    heads = lambda k: [str(x) for x in m.get_all(k, [])]
    return {"uid": uid, "message_id": str(m["Message-ID"] or "").strip(), "in_reply_to": str(m["In-Reply-To"] or "").strip(),
            "subject": " ".join(str(m["Subject"] or "").split()), "from_name": name, "from": addr.lower(), "ts": ts,
            "to": [a.lower() for _, a in getaddresses(heads("To")) if a], "cc": [a.lower() for _, a in getaddresses(heads("Cc")) if a],
            "recipient_headers": heads("To") + heads("Cc") + heads("Bcc")}


class NoLogin(Exception):
    """No Gmail login is saved for Friday yet."""


class Paused(Exception):
    """Friday is paused. Nothing is sent and no model runs."""


class DonnaMail:
    def __init__(self, cfg: config.DonnaConfig, factories=None, is_paused: Callable[[], bool] = lambda: False):
        self.cfg = cfg
        self.f = factories or mailio.Factories
        self.is_paused = is_paused

    def creds(self) -> tuple[str, str] | None:
        return config.credentials(self.cfg.secrets_file)

    def check_login(self) -> dict:
        """Log in to IMAP and SMTP and log out again. Sends nothing. {imap, smtp} as "ok" or a plain sentence."""
        c = self.creds()
        if not c:
            return {"imap": "No Gmail login is saved yet.", "smtp": "No Gmail login is saved yet."}
        return {k: config.scrub(v, self.cfg.secrets_file) for k, v in mailio.check_login(c[0], c[1], self.f).items()}

    def scan_replies(self, since: date, known_ids: set[str], head_cap: int = HEADER_CAP, folder: str = "INBOX") -> list[dict]:
        """Replies to mail Friday sent (DJ14I). Read-only, like the learner: the folder opens readonly, headers come first, and only a
        mail whose In-Reply-To names one of Friday's own Message-IDs gets its first bytes read (BODY.PEEK). Nothing is marked or moved.
        Returns [{message_id, in_reply_to, subject, from_name, ts, text}]. The text is untrusted: only code rules read it."""
        c = self.creds()
        if not c:
            raise NoLogin("Friday has no Gmail login yet.")
        if not known_ids:
            return []
        from ..donna.db import norm_message_id
        imap = self.f.imap(c[0], c[1])
        try:
            typ, _ = imap.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                return []
            typ, data = imap.uid("SEARCH", None, f"SINCE {imap_date(since)}")
            uids = sorted((int(x) for x in (data[0] if typ == "OK" and data and data[0] else b"").split()), reverse=True)[:head_cap]
            heads: list[dict] = []
            for i in range(0, len(uids), HEADER_BATCH):
                batch = uids[i:i + HEADER_BATCH]
                typ, parts = imap.uid("FETCH", ",".join(str(u) for u in batch), f"(UID BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
                if typ != "OK":
                    continue
                for item in parts or []:
                    if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                        got = re.search(rb"UID (\d+)", item[0] if isinstance(item[0], (bytes, bytearray)) else str(item[0]).encode())
                        if got:
                            heads.append(parse_header_bytes(int(got.group(1)), bytes(item[1])))
            out = []
            for h in heads:
                if norm_message_id(h["in_reply_to"]) not in known_ids:
                    continue
                typ, parts = imap.uid("FETCH", str(h["uid"]), f"(BODY.PEEK[]<0.{BODY_PARTIAL}>)")
                text = ""
                for item in parts or [] if typ == "OK" else []:
                    if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                        text = _plain_text(bytes(item[1]))
                        break
                out.append({"message_id": h["message_id"], "in_reply_to": norm_message_id(h["in_reply_to"]), "subject": h["subject"][:200],
                            "from_name": h["from_name"][:80], "from": h["from"], "ts": h["ts"], "text": text})
            return out
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass

    def scan_sent(self, folder: str, since: date, choose: Callable[[list[dict]], list[int]], head_cap: int = HEADER_CAP) -> tuple[int, dict[int, bytes]]:
        """The Sent-folder learner's read (DJ02). One read-only session:
        1. open the folder with readonly=True and search by date only;
        2. fetch HEADERS only (BODY.PEEK[HEADER.FIELDS ...]) for the newest mails;
        3. `choose(headers)` names the candidates by subject and recipients;
        4. fetch the first bytes of those mails only (BODY.PEEK).
        Nothing is marked, moved or deleted. Returns (headers seen, {uid: raw bytes of a chosen mail})."""
        c = self.creds()
        if not c:
            raise NoLogin("Friday has no Gmail login yet.")
        imap = self.f.imap(c[0], c[1])
        try:
            typ, _ = imap.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                return 0, {}
            typ, data = imap.uid("SEARCH", None, f"SINCE {imap_date(since)}")
            uids = sorted((int(x) for x in (data[0] if typ == "OK" and data and data[0] else b"").split()), reverse=True)[:head_cap]
            heads: list[dict] = []
            for i in range(0, len(uids), HEADER_BATCH):
                batch = uids[i:i + HEADER_BATCH]
                typ, parts = imap.uid("FETCH", ",".join(str(u) for u in batch), f"(UID BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
                if typ != "OK":
                    continue
                for item in parts or []:
                    if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                        got = re.search(rb"UID (\d+)", item[0] if isinstance(item[0], (bytes, bytearray)) else str(item[0]).encode())
                        if got:
                            heads.append(parse_header_bytes(int(got.group(1)), bytes(item[1])))
            wanted = [u for u in choose(heads) if u in {h["uid"] for h in heads}]
            raws: dict[int, bytes] = {}
            for u in wanted:
                typ, parts = imap.uid("FETCH", str(u), f"(BODY.PEEK[]<0.{BODY_PARTIAL}>)")
                if typ != "OK":
                    continue
                for item in parts or []:
                    if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                        raws[u] = bytes(item[1])
                        break
            return len(heads), raws
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass


def _plain_text(raw: bytes, limit: int = 4000) -> str:
    m = BytesParser(policy=policy.default).parsebytes(raw)
    part = m.get_body(preferencelist=("plain",)) if not m.is_multipart() or m.get_body(preferencelist=("plain",)) else None
    try:
        text = part.get_content() if part is not None else ""
    except Exception:  # noqa: BLE001: a body that cannot be decoded is empty text
        text = ""
    return " ".join(str(text).split())[:limit]


def sent_folder() -> str:
    return mailio.SENT_FOLDER
