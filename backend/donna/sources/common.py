"""Shared helpers for the source parsers. Standard library only: the fetch worker loads this package under `python -I`.

Everything here reads untrusted text from the web. It only turns it into plain fields; it never obeys it.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote

MAX_DESCRIPTION = 20_000
MAX_FIELD = 300
MAX_JOBS = 5_000

_ZERO_WIDTH = re.compile("[​‌‍‎‏⁠⁡⁢⁣⁤﻿­᠎‪-‮⁦-⁩]")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d]*[1-9])|opacity\s*:\s*0(?![.\d]*[1-9])", re.I)
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section", "article", "blockquote", "pre"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template", "head", "svg", "iframe", "object"}
_REMOTE = re.compile(r"\b(remote|anywhere|worldwide|distributed|work from home|wfh)\b", re.I)
_NOT_REMOTE = re.compile(r"\b(no remote|not remote|onsite only|on-site only|in-office only)\b", re.I)
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
# Off-screen, clipped or tiny text (DJ05): a person cannot read it, so it is hidden text too.
_OFFSCREEN_STYLE = re.compile(r"text-indent\s*:\s*-\d{3,}|(?:^|[;\s])(?:left|top|margin-left)\s*:\s*-\d{3,}(?:\.\d+)?px|"
                              r"clip\s*:\s*rect\(\s*0|clip-path\s*:\s*inset\(\s*50%|font-size\s*:\s*[01](?:\.\d+)?(?:px|pt)\b", re.I)
_ZERO_BOX = re.compile(r"(?:^|[;\s])(?:max-)?(?:height|width)\s*:\s*0(?:px)?\s*(?:;|$|!)", re.I)
_OVERFLOW_HIDDEN = re.compile(r"overflow\s*:\s*hidden", re.I)
_NAMED_COLOURS = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
                  "yellow": (255, 255, 0), "gray": (128, 128, 128), "grey": (128, 128, 128), "silver": (192, 192, 192),
                  "whitesmoke": (245, 245, 245), "snow": (255, 250, 250), "ivory": (255, 255, 240), "navy": (0, 0, 128)}
TRANSPARENT = "transparent"
PAGE_BACKGROUND = (255, 255, 255)
SAME_COLOUR = 16                     # channels this close count as the same colour
# Instruction-like text aimed at a model, not a person (House H7). Shared by the worker and postfacts (DJ05).
INSTRUCTION = re.compile(
    r"\bif\s+you(?:'re|\s+are)\s+(?:an?\s+)?(?:ai|a\.i\.|llm|large\s+language\s+model|language\s+model|bot|chat\s?bot|gpt|chatgpt|"
    r"claude|assistant|automated|machine)\b"
    r"|\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:of\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|preceding|other)\s+"
    r"(?:instructions?|prompts?|text|rules|directions)"
    r"|\b(?:include|mention|use|write|add|insert|say|type)\s+(?:the\s+|this\s+)?(?:secret\s+|special\s+)?(?:word|phrase|code\s?word|"
    r"keyword|password|string|emoji|sentence)\b"
    r"|\b(?:start|begin|end|sign)\s+(?:your|the)\s+(?:email|application|message|cover\s+letter|reply|answer|response)\s+(?:with|by)\b"
    r"|\b(?:ai|a\.i\.|llms?|gpts?|chatgpt|claude|language\s+models?|bots?|assistants?)\s+(?:should|must|need\s+to|are\s+asked\s+to|reading\s+this)\b"
    r"|\b(?:ai|a\.i\.|llms?|gpts?|bots?|automated)\s+(?:agents?|assistants?|models?|systems?|tools?|applicants?|bots?)\s+(?:should|must)\b"
    r"|\bautomated\s+(?:applicants?|tools?|systems?)\s*[:,]?\s*(?:please\s+)?(?:must|should|put|include|add|mention|write|use|start)\b"
    r"|\b(?:put|place)\s+(?:the\s+)?(?:code|word|phrase|string)\s+\S+\s+(?:at|in|on|as|into)\s+(?:the\s+)?(?:top|first|start|beginning|subject|opening|body)\b"
    r"|\bnote\s+(?:to|for)\s+(?:the\s+)?(?:ai|llms?|bots?|assistants?|language\s+models?|gpt|chatgpt|claude)\b"
    r"|\bsystem\s+prompt\b|\byour\s+(?:new\s+)?instructions\s+are\b", re.I)
# Poster notes (DJ05), read from a post (postfacts) and from a careers page (careers): who may apply and how.
EMAIL_ADDRESS = re.compile(r"(?<![\w.+-])([a-z0-9][a-z0-9._%+-]{0,63})@([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+)(?![\w-])", re.I)
NO_RECRUITERS = re.compile(r"\bno\s+(?:recruiters|headhunters)\b|\bno\s+(?:recruitment\s+|staffing\s+)?agenc(?:y|ies)\s*(?:,|or|and|/)\s*(?:recruiters|headhunters)\b|\brecruiters,?\s+(?:please\s+)?(?:do\s+not|don't)\b|\brecruiters\s+need\s+not\b", re.I)
NO_AGENCIES = re.compile(r"\bno\s+(?:recruitment\s+|staffing\s+)?agenc(?:y|ies)\b|\bno\s+(?:recruiters|headhunters)\s*(?:,|or|and|/)\s*(?:recruitment\s+|staffing\s+)?agenc(?:y|ies)\b|\bagencies\s+need\s+not\b|\bno\s+(?:third|3rd)[\s-]part(?:y|ies)\b", re.I)
FORM_ONLY = re.compile(r"\b(?:do\s+not|don't|please\s+don't|please\s+do\s+not)\s+(?:email|e-mail|send\s+(?:us\s+)?(?:your\s+)?(?:cv|resume|applications?|emails?))\b"
                       r"|\bapply\s+(?:only\s+)?(?:via|through|using|on)\s+(?:the|our)\s+(?:form|application\s+form|website|careers\s+page|portal|job\s+board|ats|link)\b"
                       r"|\b(?:applications?|cvs?|resumes?)\s+(?:sent\s+)?(?:by|via)\s+email\s+(?:will\s+not|won't)\s+be\b"
                       r"|\bonly\s+(?:accept|consider)\s+applications\s+(?:via|through)\s+(?:the|our)\b", re.I)
HIDDEN_FLAG = "[Friday: hidden text with instructions was removed from this post]"
MAX_HIDDEN_TEXT = 4_000


def colour(value: str):
    """(r, g, b), TRANSPARENT, or None when the value is not a colour this reader knows."""
    v = (value or "").strip().lower().replace("!important", "").strip()
    if v == TRANSPARENT:
        return TRANSPARENT
    if v in _NAMED_COLOURS:
        return _NAMED_COLOURS[v]
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m and len(m.group(1)) in (3, 4, 6, 8):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) == 8 and int(h[6:8], 16) == 0:
            return TRANSPARENT
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/]\s*([\d.]+)(%?)\s*)?\)", v)
    if m:
        if _faint(m.group(4), m.group(5)):
            return TRANSPARENT
        return tuple(min(int(m.group(i)), 255) for i in (1, 2, 3))
    m = re.fullmatch(r"hsla?\(\s*([\d.]+)(?:deg)?\s*[, ]\s*([\d.]+)%\s*[, ]\s*([\d.]+)%\s*(?:[,/]\s*([\d.]+)(%?)\s*)?\)", v)
    if m:
        if _faint(m.group(4), m.group(5)):
            return TRANSPARENT
        return _hsl(float(m.group(1)) % 360, min(float(m.group(2)), 100) / 100, min(float(m.group(3)), 100) / 100)
    return None


def _hsl(h: float, s: float, l: float) -> tuple[int, int, int]:
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    m = l - c / 2
    return round((r + m) * 255), round((g + m) * 255), round((b + m) * 255)


def _faint(alpha: str | None, percent: str | None) -> bool:
    """An alpha below 0.1 cannot be read by a person."""
    if alpha is None:
        return False
    a = float(alpha) / (100 if percent else 1)
    return a < 0.1


_LENGTH = re.compile(r"^([\d.]+)\s*(px|pt|em|rem|%|vw|vh)?$", re.I)
_SR_ONLY = {"sr-only", "visually-hidden", "visuallyhidden", "screen-reader-text", "screen-reader-only", "sr_only", "hidden", "d-none",
            "invisible", "assistive-text", "a11y-hidden"}


def _px(value: str) -> float | None:
    """A length in px (em and rem count 16, pt counts 1.33). None when it is not a plain length."""
    m = _LENGTH.match((value or "").strip().lower().replace("!important", "").strip())
    if not m:
        return None
    n, unit = float(m.group(1)), (m.group(2) or "px")
    return n * {"px": 1, "pt": 1.33, "em": 16, "rem": 16}.get(unit, 16 if unit == "%" else 16)


def style_hides(st: dict) -> bool:
    """True when the declarations make an element unreadable to a person: not shown, nearly clear, tiny, scaled away, clipped to a
    point, or pushed off the page. (Colour on colour is judged separately, since it needs the background.)"""
    if st.get("display", "").lower().replace("!important", "").strip() == "none" or st.get("visibility", "").lower().replace("!important", "").strip() == "hidden":
        return True
    try:
        if "opacity" in st and float(st["opacity"].replace("!important", "").strip().rstrip("%")) / (100 if st["opacity"].strip().endswith("%") else 1) < 0.1:
            return True
    except ValueError:
        pass
    fs = st.get("font-size", "")
    if fs and ((_px(fs) is not None and _px(fs) < 4) or re.match(r"^\s*0*\.[0-2]\d*\s*(?:em|rem)\b", fs, re.I)):
        return True
    if re.search(r"scale(?:[XY]|3d)?\(\s*(?:0|0?\.0\d*)\s*[,)]", st.get("transform", ""), re.I):
        return True
    tiny = any(_px(st.get(k, "")) is not None and _px(st.get(k, "")) <= 1 for k in ("width", "height", "max-width", "max-height"))
    if tiny and (st.get("overflow", "").lower().replace("!important", "").strip().startswith("hidden") or "clip" in st):
        return True
    clip = re.match(r"rect\(\s*([^)]*)\)", st.get("clip", ""), re.I)
    if clip:
        nums = [_px(x) for x in re.split(r"[\s,]+", clip.group(1).strip()) if x]
        if nums and all(n is not None and n <= 1 for n in nums):
            return True
    return False


def _hidden_classes(raw: str) -> set[str]:
    """Class names that a `<style>` block of the page hides (a simple `.name { display:none }` rule), plus the usual
    screen-reader-only names. A person cannot read what those classes wrap."""
    found = set(_SR_ONLY)
    for block in re.findall(r"<style[^>]*>(.*?)</style>", raw or "", re.I | re.S)[:5]:
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)[:50_000]
        for sel, decl in re.findall(r"([^{}@]+)\{([^{}]*)\}", block):
            if style_hides(_style(decl)):
                for one in sel.split(","):
                    m = re.fullmatch(r"\s*(?:[a-z][a-z0-9]*)?\.([A-Za-z_][\w-]*)\s*", one)
                    if m:
                        found.add(m.group(1).lower())
    return found


def _style(style: str) -> dict[str, str]:
    out = {}
    for part in (style or "").split(";"):
        k, sep, v = part.partition(":")
        if sep:
            out[k.strip().lower()] = v.strip()
    return out


def _background(st: dict, attrs: dict):
    if "background-color" in st:
        return colour(st["background-color"])
    for token in re.split(r"\s+(?![^(]*\))", st.get("background", "")):
        c = colour(token)
        if c is not None:
            return c
    return colour(attrs.get("bgcolor", ""))


def same_colour(fg, bg) -> bool:
    if fg == TRANSPARENT:
        return True
    if fg is None or bg is None or bg == TRANSPARENT:
        return False
    return max(abs(a - b) for a, b in zip(fg, bg)) <= SAME_COLOUR


class _Text(HTMLParser):
    """HTML to plain text. Hidden elements are dropped and counted: hidden text in a job post is a warning sign (DJ05 flags it).
    Hidden means: hidden attributes and styles, off-screen or tiny text, and text in the same colour as its background."""

    def __init__(self, hidden_classes: set[str] | None = None):
        super().__init__(convert_charrefs=True)
        self.hidden_classes = hidden_classes or set(_SR_ONLY)
        self.out: list[str] = []
        self.links: list[str] = []
        self.stack: list[tuple[str, bool, object]] = []      # (tag, hides its content, background colour for its children)
        self.hidden = 0
        self.hidden_text: list[str] = []
        self._hidden_len = 0

    def _hiding(self) -> bool:
        return any(h for _t, h, _bg in self.stack)

    def _bg(self):
        for _t, _h, bg in reversed(self.stack):
            if bg is not None and bg != TRANSPARENT:
                return bg
        return PAGE_BACKGROUND

    def _hides(self, tag: str, a: dict) -> tuple[bool, object]:
        raw = a.get("style", "")
        st = _style(raw)
        bg = _background(st, a)
        if "hidden" in a or a.get("aria-hidden", "").lower() == "true" or _HIDDEN_STYLE.search(raw) or _OFFSCREEN_STYLE.search(raw) \
                or (_ZERO_BOX.search(raw) and _OVERFLOW_HIDDEN.search(raw)) or style_hides(st) \
                or any(c.lower() in self.hidden_classes for c in a.get("class", "").split()):
            return True, bg
        fg = colour(st["color"]) if "color" in st else colour(a.get("color", "")) if tag == "font" else None
        if fg is not None:
            under = bg if bg is not None and bg != TRANSPARENT else self._bg()
            if same_colour(fg, under):
                return True, bg
        return False, bg

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        hides, bg = (True, None) if tag in _SKIP else self._hides(tag, a)
        if hides and tag not in _SKIP:
            self.hidden += 1
        if tag == "a" and a.get("href") and not self._hiding() and not hides:
            self.links.append(a["href"][:500])
        if tag in _BLOCK:
            self.out.append("\n")
        if tag not in _VOID:
            self.stack.append((tag, hides, bg))

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._hiding():
            self.out.append(data)
        elif self._hidden_len < MAX_HIDDEN_TEXT and not any(t in ("script", "style") for t, _h, _bg in self.stack):
            self.hidden_text.append(data)            # kept only to look for instructions, never shown or stored
            self._hidden_len += len(data)


def html_to_text(raw: str) -> tuple[str, list[str], int]:
    """(plain text, link targets, number of hidden elements removed). When the hidden text held instruction-like words, the text
    ends with the fixed line HIDDEN_FLAG (DJ05 flags the post for the owner). The hidden text itself is never returned."""
    p = _Text(_hidden_classes(raw))
    try:
        p.feed(raw or "")
        p.close()
    except Exception:  # noqa: BLE001: a broken page gives what was read so far
        pass
    text = _ZERO_WIDTH.sub("", "".join(p.out)).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    hidden_words = re.sub(r"\s+", " ", _ZERO_WIDTH.sub("", "".join(p.hidden_text)))
    if INSTRUCTION.search(hidden_words):
        return (text[:MAX_DESCRIPTION - len(HIDDEN_FLAG) - 2] + "\n\n" + HIDDEN_FLAG).strip(), p.links, p.hidden
    return text[:MAX_DESCRIPTION], p.links, p.hidden


def plain(value, limit: int = MAX_FIELD) -> str:
    """One line of plain text: no tags, no zero-width characters, no control characters."""
    if value is None:
        return ""
    s = html.unescape(str(value))
    if "<" in s and ">" in s:
        s = html_to_text(s)[0]
    s = _ZERO_WIDTH.sub("", s)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:limit]


def date_of(value) -> str:
    """YYYY-MM-DD from epoch seconds or milliseconds, ISO 8601 or RFC 822. Empty when unknown."""
    if value in (None, "", 0):
        return ""
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            n = float(value)
            if n > 1e11:
                n /= 1000.0
            return datetime.fromtimestamp(n, timezone.utc).strftime("%Y-%m-%d")
        s = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError, IndexError):
        return ""


def remote_flag(*texts, explicit=None) -> int:
    """1 remote, 0 not. An explicit field wins; else words in the location or title."""
    if explicit is not None:
        if isinstance(explicit, str):
            return 1 if explicit.strip().lower() in ("remote", "true", "yes", "1") else 0
        return 1 if explicit else 0
    t = " ".join(str(x or "") for x in texts)
    return 1 if _REMOTE.search(t) and not _NOT_REMOTE.search(t) else 0


def web_url(value) -> str:
    """An http(s) address as given, or empty. Never anything else (no javascript:, no mailto:)."""
    s = plain(value, 1000)
    return s if re.match(r"^https?://[^\s/]+", s, re.I) else ""


def first_url(text: str, skip_hosts: tuple[str, ...] = ()) -> str:
    for m in _URL.finditer(text or ""):
        u = m.group(0).rstrip(".,;:")
        host = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].lower()
        if not any(host == h or host.endswith("." + h) for h in skip_hosts):
            return u
    return ""


def load_json(text: str):
    return json.loads(text)


def job(source: str, ext_id, company, title, location="", remote=0, posted="", job_url="", apply_url="", description="",
        hidden=0, extra: dict | None = None) -> dict | None:
    """One job in Friday's shape. None when it has no id, no title or no address to look at."""
    ext = plain(ext_id, 200)
    t = plain(title)
    ju = web_url(job_url)
    au = web_url(apply_url) or ju
    if not ext or not t or not (ju or au):
        return None
    out = {"source": source, "ext_id": ext, "company": plain(company), "title": t, "location": plain(location),
           "remote": int(bool(remote)), "posted": date_of(posted) if posted else "", "job_url": ju or au, "apply_url": au,
           "description": (description or "")[:MAX_DESCRIPTION], "hidden": int(hidden or 0)}
    if extra:
        out["extra"] = {str(k)[:40]: plain(v) for k, v in list(extra.items())[:10]}
    return out


def text_of(raw_html: str) -> tuple[str, int]:
    """Description text and hidden-element count from an HTML fragment (possibly entity-escaped, as Greenhouse sends it)."""
    s = raw_html or ""
    if "&lt;" in s and "<" not in s:
        s = html.unescape(s)
    text, _links, hidden = html_to_text(s)
    return text, hidden


def q(value: str) -> str:
    return quote(value or "", safe="")


def collect(items, make) -> list[dict]:
    """Run `make` over each item, keep the good ones, at most MAX_JOBS. One bad item never stops the rest."""
    out = []
    for it in items or []:
        if len(out) >= MAX_JOBS:
            break
        if not isinstance(it, dict):
            continue
        try:
            j = make(it)
        except (KeyError, TypeError, ValueError, AttributeError):
            j = None
        if j:
            out.append(j)
    return out
