"""Friday's side of the shared ledger in `~/.local/share/shared/ledger/` (PLAN-FINAL rows 37 and 46).

- `Friday.json` is Friday's file: `{"entries": [{domain, agent, first_contact, last_contact, suppressed}]}`. Dates are ISO days. It holds
  domains and days only: no address, no name, no text. another tool's reader takes this shape.
- `another tool.json` (same shape) and `the mail module.json` (`{"domains": [...], "suppressed": [...]}`) are read, never written. A missing file is empty.
  A file that is not a plain file, too big, or not readable stops the send (`LedgerError`): Friday cannot tell who owns the domain.
- `reserve()` writes Friday's row for a domain BEFORE any delivery, under a thread lock plus a file lock, after it reads the other files
  again. A domain suppressed by any agent is refused for good. A domain another agent used in the last 90 days is refused unless the
  owner said OK. the mail module's file has no dates: a the mail module contact always counts as recent.
- Writes go through a random temp name opened with `O_CREAT | O_EXCL | O_NOFOLLOW` (mode 600), then a rename.
"""
from __future__ import annotations

import datetime as _dt
try:
    import fcntl
except ImportError:          # Windows has no fcntl: the in-process lock still holds
    fcntl = None
import json
import os
import secrets as _random
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from .domains import registrable

AGENT = "donna"
OTHERS = ()           # other tools' ledgers: none in this copy
COOL_OFF_DAYS = 90
MAX_BYTES = 2 * 1024 * 1024
LOCK_FILE = ".donna.lock"

_lock = threading.Lock()


class LedgerError(RuntimeError):
    """A ledger file cannot be trusted or read. The text is a plain sentence."""


class LedgerRefused(RuntimeError):
    """Another agent holds this domain. `kind` is `suppressed` (never) or `other_agent` (the owner can say OK)."""

    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind


@dataclass(frozen=True)
class Entry:
    agent: str
    domain: str
    contacted: bool
    suppressed: bool
    last_contact: str | None = None


def default_dir(home) -> Path:
    if os.environ.get("DONNA_LEDGER_DIR"):          # a stand-alone install keeps the ledger beside its data
        return Path(os.environ["DONNA_LEDGER_DIR"])
    return Path(home) / ".local" / "share" / "donna" / "ledger"


def norm(domain: str) -> str:
    d = (domain or "").strip().lower().rsplit("@", 1)[-1].strip(".")
    return registrable(d) if "." in d else d


def _read_json(path: Path):
    """No link, a plain file, at most 2 MB. Missing = None."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        raise LedgerError(f"{path.name} cannot be opened safely.") from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            raise LedgerError(f"{path.name} is not a small plain file.")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            return json.loads(fh.read(MAX_BYTES + 1).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise LedgerError(f"{path.name} is not valid JSON.") from e
    finally:
        if fd >= 0:
            os.close(fd)


def _entries(agent: str, data) -> list[Entry]:
    """the mail module: `{"domains", "suppressed"}`. Friday and another tool: entries, as a list or under "entries"."""
    if data is None:
        return []
    out: list[Entry] = []
    if isinstance(data, dict) and "entries" not in data and ("domains" in data or "suppressed" in data):
        sup = {norm(str(d)) for d in data.get("suppressed") or []}
        for d in data.get("domains") or []:
            out.append(Entry(agent, norm(str(d)), True, norm(str(d)) in sup))
        for d in sup - {e.domain for e in out}:
            out.append(Entry(agent, d, False, True))
        return out
    rows = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get("domain"), str) for r in rows):
        raise LedgerError(f"The {agent} ledger has an unknown shape.")
    for r in rows:
        last = r.get("last_contact")
        out.append(Entry(agent, norm(r["domain"]), bool(last or r.get("first_contact")), bool(r.get("suppressed")),
                         str(last) if last else None))
    return out


def _recent(when: str | None, today: _dt.date) -> bool:
    try:
        return (today - _dt.date.fromisoformat(str(when)[:10])).days < COOL_OFF_DAYS
    except ValueError:
        return True                      # no readable day: count it as recent (the stricter side)


class Ledger:
    def __init__(self, directory: Path, tz=None):
        self.dir = Path(directory)
        self.tz = tz

    def day(self, now: float) -> _dt.date:
        return _dt.datetime.fromtimestamp(now, self.tz).date()

    # ---- reading ----
    def others(self) -> list[Entry]:
        out: list[Entry] = []
        for name in OTHERS:
            out += _entries(name, _read_json(self.dir / f"{name}.json"))
        return out

    def mine(self) -> list[dict]:
        data = _read_json(self.dir / f"{AGENT}.json")
        if data is None:
            return []
        rows = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get("domain"), str) for r in rows):
            raise LedgerError("donna.json has an unknown shape.")
        return rows

    def problem(self, domain: str, now: float, ok_other_agent: bool = False) -> tuple[str, str] | None:
        """(kind, sentence) when another agent blocks this domain, else None. `kind`: suppressed | other_agent."""
        d = norm(domain)
        today = self.day(now)
        mine = next((r for r in self.mine() if norm(r["domain"]) == d), None)
        if mine and mine.get("suppressed"):
            return "suppressed", f"{d} asked to stop. Nobody writes to it again."
        hits = [e for e in self.others() if e.domain == d]
        for e in hits:
            if e.suppressed:
                return "suppressed", f"{d} asked {e.agent.capitalize()} to stop. It is blocked for every agent."
        if not ok_other_agent:
            for e in hits:
                if e.contacted and (e.last_contact is None or _recent(e.last_contact, today)):
                    return "other_agent", (f"{e.agent.capitalize()} wrote to {d} in the last {COOL_OFF_DAYS} days. "
                                           "Press Approve again and tick that you know, if you want to send it.")
        return None

    # ---- writing ----
    def _write(self, rows: list[dict]) -> None:
        tmp = self.dir / f".{AGENT}.{_random.token_hex(8)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"entries": rows}, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.dir / f"{AGENT}.json")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _locked(self):
        ledger = self

        class _Locked:
            def __enter__(self):
                ledger.dir.mkdir(parents=True, exist_ok=True)
                os.chmod(ledger.dir, 0o700)
                _lock.acquire()
                try:
                    self.fd = os.open(ledger.dir / LOCK_FILE, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    if fcntl:
                        fcntl.flock(self.fd, fcntl.LOCK_EX)
                except BaseException:
                    _lock.release()
                    raise
                return self

            def __exit__(self, *exc):
                try:
                    if fcntl:
                        fcntl.flock(self.fd, fcntl.LOCK_UN)
                    os.close(self.fd)
                finally:
                    _lock.release()
        return _Locked()

    def reserve(self, domain: str, now: float, ok_other_agent: bool = False) -> dict | None:
        """Hold `domain` for Friday before a send. Returns Friday's row as it was before (None when new), for `restore`.
        Raises LedgerRefused or LedgerError, and then writes nothing."""
        d = norm(domain)
        if not d:
            raise LedgerError("No domain to reserve.")
        today = self.day(now).isoformat()
        with self._locked():
            blocked = self.problem(d, now, ok_other_agent)
            if blocked:
                raise LedgerRefused(*blocked)
            rows = self.mine()
            before = None
            for r in rows:
                if norm(r["domain"]) == d:
                    before = dict(r)
                    r["last_contact"] = today
                    r["first_contact"] = r.get("first_contact") or today
                    break
            else:
                rows.append({"domain": d, "agent": AGENT, "first_contact": today, "last_contact": today, "suppressed": False})
            self._write(rows)
            return before

    def restore(self, domain: str, before: dict | None) -> None:
        """Put Friday's row back as it was. Used only when the mail host was never reached, so nothing was sent."""
        d = norm(domain)
        with self._locked():
            rows = [r for r in self.mine() if norm(r["domain"]) != d]
            if before is not None:
                rows.append(before)
            self._write(rows)

    def mark_suppressed(self, domain: str) -> None:
        """This domain asked Friday to stop. Kept for good; another tool and the mail module's readers see it."""
        d = norm(domain)
        if not d:
            raise LedgerError("No domain to suppress.")
        with self._locked():
            rows = self.mine()
            for r in rows:
                if norm(r["domain"]) == d:
                    r["suppressed"] = True
                    break
            else:
                rows.append({"domain": d, "agent": AGENT, "first_contact": None, "last_contact": None, "suppressed": True})
            self._write(rows)
