"""the mail module's Gmail login, kept in a private file (mode 600). The app password is never given back, never logged."""
from __future__ import annotations

import os
import re
from pathlib import Path

from .db import EMAIL_OK

KEY_ADDRESS = "PA_GMAIL_ADDRESS"
KEY_PASSWORD = "PA_GMAIL_APP_PASSWORD"


def read(path: Path) -> dict:
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def clean_password(raw: str) -> str:
    """Google shows the app password as four groups of four letters. Spaces are not part of it."""
    return re.sub(r"\s+", "", raw or "")


def validate(address: str, password: str) -> str | None:
    """A plain sentence when something is wrong, else None."""
    if not EMAIL_OK.match((address or "").strip()):
        return "The Gmail address does not look right. Use the full address, for example name@gmail.com."
    if not re.fullmatch(r"[A-Za-z0-9]{16}", clean_password(password)):
        return "An app password has 16 letters. Make one at myaccount.google.com/apppasswords and paste it here."
    return None


def save(path: Path, address: str, password: str) -> None:
    """Write the file in one step with mode 600. Other keys in the file stay."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    keep = {k: v for k, v in read(path).items() if k not in (KEY_ADDRESS, KEY_PASSWORD)}
    keep[KEY_ADDRESS] = address.strip()
    keep[KEY_PASSWORD] = clean_password(password)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("".join(f"{k}={v}\n" for k, v in keep.items()))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def credentials(path: Path) -> tuple[str, str] | None:
    d = read(path)
    a, p = d.get(KEY_ADDRESS, ""), clean_password(d.get(KEY_PASSWORD, ""))
    return (a, p) if a and p else None


def scrub(text: str, path: Path) -> str:
    """Remove the app password from any text before it is logged or shown."""
    c = credentials(path)
    if c and c[1]:
        text = text.replace(c[1], "[password hidden]")
    return text
