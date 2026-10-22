"""Friday's folders and her private Gmail login (`~/.config/Friday/secrets.env`, mode 600).

The login is checked with the mail module's `secrets_file.validate` and written the way her `save` writes (one step, mode 600,
`O_EXCL | O_NOFOLLOW`). Her `save` has the key names `PA_*` built in and a reviewed file must not change, so the write is
mirrored here with Friday's own key names. The app password is never returned and never logged.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..assistant import secrets_file

KEY_ADDRESS = "DONNA_GMAIL_ADDRESS"
KEY_PASSWORD = "DONNA_GMAIL_APP_PASSWORD"
UA_DEFAULT = "DonnaJobSearch/1.0 (a personal job-search tool; reads public job pages, one request at a time)"


@dataclass
class DonnaConfig:
    data_dir: Path
    config_dir: Path           # private folder, outside the Desktop so a background job can read it
    sim: bool = False
    user_agent: str = UA_DEFAULT

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "donna.db"

    @property
    def work_dir(self) -> Path:
        return Path(self.data_dir) / "donna"

    @property
    def blocklist_file(self) -> Path:
        """The owner's private blocklist (DJ03). In data/, never in code."""
        return self.work_dir / "blocklist.txt"

    @property
    def off_file(self) -> Path:
        return Path(self.data_dir) / "DONNA_OFF"

    @property
    def secrets_file(self) -> Path:
        return Path(self.config_dir) / "secrets.env"


def from_settings(settings, config_dir: Path | None = None) -> DonnaConfig:
    """`DONNA_CONFIG_DIR` (tests, the sandbox) or `<home>/.config/Friday`."""
    cfg = config_dir or os.environ.get("DONNA_CONFIG_DIR") or Path(settings.home) / ".config" / "donna"
    return DonnaConfig(Path(settings.data_dir), Path(cfg), bool(getattr(settings, "sim", False)))


def save_login(path: Path, address: str, password: str) -> str | None:
    """Check, then write. A plain sentence when something is wrong, else None. Other keys in the file stay."""
    problem = secrets_file.validate(address, password)
    if problem:
        return problem
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    keep = {k: v for k, v in secrets_file.read(path).items() if k not in (KEY_ADDRESS, KEY_PASSWORD)}
    keep[KEY_ADDRESS] = address.strip()
    keep[KEY_PASSWORD] = secrets_file.clean_password(password)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("".join(f"{k}={v}\n" for k, v in keep.items()))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return None


def credentials(path: Path) -> tuple[str, str] | None:
    d = secrets_file.read(path)
    a, p = d.get(KEY_ADDRESS, ""), secrets_file.clean_password(d.get(KEY_PASSWORD, ""))
    return (a, p) if a and p else None


def address(path: Path) -> str:
    """The Gmail address only (it is not a secret). Empty when no login is saved."""
    c = credentials(path)
    return c[0] if c else ""


def password_pattern(password: str):
    """The password as it may appear in a text: Google shows it in four groups, so spaces, line breaks and hyphens between
    any two letters still match (DJ10 L3)."""
    return re.compile(r"[\s\-]*".join(re.escape(ch) for ch in password), re.I)


def holds_password(text: str, password: str) -> bool:
    return bool(password) and bool(password_pattern(password).search(text or ""))


def scrub(text: str, path: Path) -> str:
    """Remove the app password from any text before it is logged or shown."""
    c = credentials(path)
    if c and c[1]:
        text = password_pattern(c[1]).sub("[password hidden]", text)
    return text
