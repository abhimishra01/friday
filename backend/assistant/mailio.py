"""The only file that talks to Gmail. Standard library only.

- `deliver()` is the ONLY place that sends through SMTP (`check_login` also logs in, but sends nothing). Only `sender.Sender` calls it (a test checks this).
- IMAP is read-only: the mailbox is opened with readonly=True and messages are fetched with BODY.PEEK[],
  so nothing is marked as read, moved or deleted.
Tests replace `imap_factory` and `smtp_factory` with fakes.
"""
from __future__ import annotations

import imaplib
import io
import mimetypes
import os
import re
import smtplib
import socket
import ssl
import stat
import zipfile
import zlib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime, formatdate, make_msgid
from pathlib import Path

IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
SENT_FOLDER = "[Gmail]/Sent Mail"
MAX_RAW = 12 * 1024 * 1024
MAX_SAVE = 10 * 1024 * 1024
TIMEOUT = 60              # seconds for one network read; Gmail is sometimes slow
BATCH = 15                # messages per folder per check; the rest comes at the next check
NETWORK_ERRORS = (socket.timeout, TimeoutError, socket.gaierror, ConnectionError, ssl.SSLError, imaplib.IMAP4.abort,
                  smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)


def is_network_error(e: BaseException) -> bool:
    """True for a dropped or slow connection (not a refused login): worth trying again later."""
    if isinstance(e, (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError)) and not isinstance(e, imaplib.IMAP4.abort):
        return False
    return isinstance(e, NETWORK_ERRORS) or (isinstance(e, OSError) and not isinstance(e, (FileNotFoundError, PermissionError)))


def is_auth_error(e: BaseException) -> bool:
    """True when the mail host refused the sign-in."""
    return isinstance(e, (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError)) and not isinstance(e, imaplib.IMAP4.abort)


class SimBlocked(RuntimeError):
    """the mail module runs in sim mode (the mail module_SIM=1): a real mail host is never opened. Not a network error, so nothing retries."""


def sim_on() -> bool:
    return os.environ.get("DONNA_SIM", "").strip().lower() in ("1", "true", "yes", "on")


class ConnectFailed(Exception):
    """The connection to Gmail failed BEFORE any mail was handed over, so trying again cannot send twice."""


def imap_factory(address: str, password: str):
    if sim_on():
        raise SimBlocked("Sim mode: a real mail server is never opened.")
    ctx = ssl.create_default_context()
    c = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx, timeout=TIMEOUT)
    c.login(address, password)
    return c


def smtp_factory(address: str, password: str):
    if sim_on():
        raise SimBlocked("Sim mode: a real mail server is never opened.")
    ctx = ssl.create_default_context()
    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=TIMEOUT)
    s.login(address, password)
    return s


class Factories:
    """Holder, so tests can swap the two factories on one object."""
    imap = staticmethod(imap_factory)
    smtp = staticmethod(smtp_factory)


def check_login(address: str, password: str, f: type = Factories) -> dict:
    """Log in to IMAP and SMTP. Sends nothing. Returns {imap, smtp} as "ok" or a plain error sentence."""
    out = {}
    for name, fac, close in (("imap", f.imap, "logout"), ("smtp", f.smtp, "quit")):
        try:
            c = fac(address, password)
            try:
                getattr(c, close)()
            except Exception:
                pass
            out[name] = "ok"
        except Exception as e:  # noqa: BLE001: the wording is for the page
            msg = str(e).replace(password, "[password hidden]")
            out[name] = "Gmail refused the login. Check the address and the app password." \
                if "auth" in msg.lower() or "credential" in msg.lower() or "invalid" in msg.lower() \
                else f"Could not connect ({type(e).__name__}). Check the internet connection."
    return out


class AttachmentRefused(ValueError):
    """A file that must never be attached. The text is a plain sentence."""


MAX_ATTACH = 5 * 1024 * 1024
ZIP_SCAN_MEMBER, ZIP_SCAN_TOTAL = 20 * 1024 * 1024, 40 * 1024 * 1024
ZIP_LIKE = (".zip", ".docx", ".xlsx", ".pptx")
MAGIC = {".png": (b"\x89PNG\r\n\x1a\n",), ".pdf": (b"%PDF-",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
         **{e: (b"PK\x03\x04",) for e in ZIP_LIKE}}
TEXT_TYPES = (".html", ".csv")


def _open_nofollow(path: Path, root: Path | None) -> int:
    """A file descriptor for `path`. With a `root` (a real, resolved folder that holds the path), every folder below the root is opened
    one by one with O_NOFOLLOW from the root's descriptor, so a folder that was swapped for a link after the check is not followed (R4)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if root is not None and os.open in getattr(os, "supports_dir_fd", set()) and hasattr(os, "O_DIRECTORY"):
        rel = Path(os.path.abspath(path)).relative_to(os.path.abspath(root)).parts
        if rel:
            dfd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in rel[:-1]:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dfd)
                    os.close(dfd)
                    dfd = nxt
                return os.open(rel[-1], flags, dir_fd=dfd)
            finally:
                os.close(dfd)
    return os.open(str(path), flags)


def read_attachment(path: Path, root: Path | None = None) -> bytes:
    """RB5 (C2): the bytes of one attachment, read through one file descriptor opened with O_NOFOLLOW (and, with `root`, through folders
    opened with O_NOFOLLOW too). It must be a regular file with
    one hard link that belongs to this user (a hard link to another file, such as a secrets file, is refused), at most 5 MB, and its
    first bytes must fit its ending (a .png is a PNG, a .zip is a zip, a .html or .csv is text). Raises AttachmentRefused."""
    name = Path(path).name[:60]
    try:
        fd = _open_nofollow(path, root)
    except (OSError, ValueError):
        raise AttachmentRefused(f"Attachment {name!r} is not allowed or does not exist.")
    try:
        st = os.fstat(fd)
        uid = getattr(os, "getuid", None)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or (uid is not None and st.st_uid != uid()):
            raise AttachmentRefused(f"Attachment {name!r} is not allowed: it must be a plain file of its own.")
        if st.st_size > MAX_ATTACH:
            raise AttachmentRefused(f"Attachment {name!r} is {st.st_size // 1024} KB. The limit is 5 MB in total.")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(MAX_ATTACH + 1)
    finally:
        os.close(fd)
    check_attachment_bytes(path, data)
    return data


def check_attachment_bytes(path, data: bytes) -> None:
    """The size and first-bytes rules for the content of one attachment (also used for bytes that a caller read and checked itself)."""
    name = Path(path).name[:60]
    if len(data) > MAX_ATTACH:
        raise AttachmentRefused(f"Attachment {name!r} is too big.")
    ext = Path(path).suffix.lower()
    if ext in MAGIC and not data.startswith(MAGIC[ext]):
        raise AttachmentRefused(f"Attachment {name!r} is not a real {ext[1:]} file.")
    if ext in TEXT_TYPES:
        try:
            data[:8192].decode("utf-8", errors="strict")
        except UnicodeDecodeError as e:
            if e.start < len(data[:8192]) - 4:          # a cut in the middle of a letter at the end of the sample is fine
                raise AttachmentRefused(f"Attachment {name!r} is not a text file.")
        if b"\x00" in data[:8192]:
            raise AttachmentRefused(f"Attachment {name!r} is not a text file.")


def holds_secret(path: Path, data: bytes, secret: str) -> bool:
    """RB5 (C2): True when the app password is in the bytes of the file, or in a member of a zip or Office file."""
    if not secret:
        return False
    key = secret.encode()
    if key in data:
        return True
    if Path(path).suffix.lower() in ZIP_LIKE:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                total = 0
                for i in z.infolist():
                    total += i.file_size
                    if i.file_size > ZIP_SCAN_MEMBER or total > ZIP_SCAN_TOTAL:
                        raise AttachmentRefused(f"Attachment {Path(path).name[:60]!r} is too large inside to check.")
                    if i.flag_bits & 0x1:
                        raise AttachmentRefused(f"Attachment {Path(path).name[:60]!r} has an encrypted file that Donna cannot check.")
                    if key in z.read(i):
                        return True
        except AttachmentRefused:
            raise
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, ValueError, zlib.error):
            raise AttachmentRefused(f"Attachment {Path(path).name[:60]!r} is not a zip that Donna can read.")
    return False


def build_message(sender: str, to: str, subject: str, body: str, attachments: list,
                  in_reply_to: str = "", references: str = "") -> EmailMessage:
    m = EmailMessage(policy=policy.SMTP)
    m["From"] = sender
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = (references + " " + in_reply_to).strip() if in_reply_to not in references else references
    m.set_content(body)
    for p in attachments:
        # A (path, bytes) pair is a file the sender has read and checked already; a path is read safely here.
        path, data, *rest = p if isinstance(p, tuple) else (p, read_attachment(p))
        ctype = rest[0] if rest and rest[0] else mimetypes.guess_type(path.name)[0]
        main, sub = (ctype or "application/octet-stream").split("/", 1)
        m.add_attachment(data, maintype=main, subtype=sub, filename=path.name)
    return m


def deliver(creds: tuple[str, str], msg: EmailMessage, f: type = Factories) -> None:
    """Open SMTP and send one message. The one and only SMTP call in the program."""
    try:
        s = f.smtp(creds[0], creds[1])
    except Exception as e:  # noqa: BLE001
        if is_network_error(e):
            raise ConnectFailed(f"{type(e).__name__}: {e}") from e
        raise
    try:
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass


# ---- reading ----

def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def authenticated(auth: list[str], from_addr: str) -> bool:
    """True when Gmail's own Authentication-Results header (the topmost mx.google.com one) shows that the sender is who
    the From line says: dmarc=pass, or dkim=pass / spf=pass for the sender's own domain (PA1R M3)."""
    dom = from_addr.rsplit("@", 1)[-1].lower()
    same = lambda d: bool(d) and (d.lower().strip(";") == dom or d.lower().strip(";").endswith("." + dom) or dom.endswith("." + d.lower().strip(";")))
    for h in auth:
        if not h.lower().startswith("mx.google.com"):
            continue
        if re.search(r"\bdmarc=pass\b", h, re.I):
            return True
        for m in re.finditer(r"\bdkim=pass\b[^;]*?header\.(?:d|i)=@?(\S+)", h, re.I):
            if same(m.group(1).split("@")[-1]):
                return True
        for m in re.finditer(r"\bspf=pass\b[^;]*?smtp\.mailfrom=(\S+)", h, re.I):
            if same(m.group(1).split("@")[-1]):
                return True
        return False          # only the topmost Gmail header counts
    return False


def parse_raw(raw: bytes) -> dict | None:
    """One stored-ready record from a raw email, or None when it cannot be read."""
    if len(raw) > MAX_RAW:
        return None
    try:
        m = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        return None
    body = ""
    try:
        part = m.get_body(preferencelist=("plain", "html"))
        if part is not None:
            body = part.get_content()
            if part.get_content_type() == "text/html":
                body = html_to_text(body)
    except Exception:
        body = ""
    atts = []
    for p in m.iter_attachments():
        data = p.get_payload(decode=True) or b""
        atts.append({"name": p.get_filename() or "attachment", "size": len(data), "data": data if len(data) <= MAX_SAVE else None})
    try:
        ts = parsedate_to_datetime(m["Date"]).timestamp()
    except Exception:
        ts = None
    return {
        "message_id": (m["Message-ID"] or "").strip(), "in_reply_to": (m["In-Reply-To"] or "").strip(),
        "refs": " ".join((m["References"] or "").split()), "subject": str(m["Subject"] or "").replace("\r", " ").replace("\n", " "),
        "from": parseaddr(str(m["From"] or ""))[1].lower(),
        "to": [a.lower() for _, a in getaddresses([str(x) for x in m.get_all("To", [])])],
        "cc": [a.lower() for _, a in getaddresses([str(x) for x in m.get_all("Cc", [])])],
        "body": body, "attachments": atts, "ts": ts,
        "auth": [" ".join(str(x).split()) for x in m.get_all("Authentication-Results", [])],
        "auto": (m["Auto-Submitted"] or "no").strip().lower() != "no" or (m["Precedence"] or "").strip().lower() in ("bulk", "junk", "list")
        or parseaddr(str(m["From"] or ""))[1].lower().startswith(("mailer-daemon", "postmaster", "no-reply", "noreply")),
    }


def fetch_new(imap, folder: str, criterion: str, address: str, last_uid: int, on_row=None, limit: int = BATCH) -> list[tuple[int, bytes]]:
    """Messages in `folder` matching `criterion` (for example 'FROM "a@b.com"') with uid > last_uid, oldest first,
    at most `limit` per call (the rest comes next time). Read-only: the folder is opened readonly and every fetch uses
    BODY.PEEK. `on_row(uid, raw)` is called as each message arrives, so a failure half way loses nothing."""
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        return []
    typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*", criterion)
    if typ != "OK" or not data or not data[0]:
        return []
    out = []
    for uid in sorted(int(x) for x in data[0].split() if int(x) > last_uid)[:limit]:
        typ, parts = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            continue
        for item in parts:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], (bytes, bytearray)):
                out.append((uid, bytes(item[1])))
                if on_row:
                    on_row(uid, bytes(item[1]))
                break
    return out
