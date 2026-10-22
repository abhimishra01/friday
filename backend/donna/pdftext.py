"""Text from a PDF, standard library only (The Office installs no PDF package, DJ03).

Enough for a CV made by Word, Google Docs, LaTeX or a browser's "Save as PDF": plain and compressed objects, object streams,
Flate, ASCIIHex and ASCII85 filters, ToUnicode maps (also two-byte fonts), form XObjects. Images are skipped, so a scanned CV gives
no text. A locked (encrypted) PDF is refused. Every limit is fixed: 16 MB inflated in total, 400,000 characters out, 5 levels of
forms. The file is the owner's own upload; it is parsed here and nothing in it is ever run.
"""
from __future__ import annotations

import base64
import re
import unicodedata
import zlib

MAX_INFLATE = 16 * 1024 * 1024
MAX_CHARS = 400_000
MAX_FORM_DEPTH = 5


class PdfError(Exception):
    """A plain sentence for the page."""


class Name(str):
    pass


class Ref(tuple):
    pass


class Op(str):
    """An operator or keyword in a content stream (Tj, BT, obj, R ...)."""


_SKIP_RE = re.compile(rb"(?:[ \t\r\n\f\x00]+|%[^\r\n]*)*")
_REGULAR_RE = re.compile(rb"[^ \t\r\n\f\x00()<>\[\]{}/%]*")
_LITERAL_SPECIAL_RE = re.compile(rb"[\\()]")
_OBJ_RE = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")


# ---- the lexer ----

class Lexer:
    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.i, self.n = data, pos, len(data)

    def _skip(self):                                          # white space and % comments
        self.i = _SKIP_RE.match(self.d, self.i).end()

    def token(self):
        """The next token, or None at the end. Strings come back as bytes, names as Name, keywords as Op."""
        d, n = self.d, self.n
        self._skip()
        while self.i < n and d[self.i] == 0x29:               # stray ")" characters are skipped
            self.i += 1
            self._skip()
        if self.i >= n:
            return None
        c = d[self.i]
        if c == 0x28:                                         # (
            return self._literal()
        if c == 0x3C:                                         # <
            if d[self.i:self.i + 2] == b"<<":
                self.i += 2
                return Op("<<")
            j = d.find(b">", self.i)
            j = n if j < 0 else j
            raw = re.sub(rb"[^0-9A-Fa-f]", b"", d[self.i + 1:j])
            self.i = j + 1
            if len(raw) % 2:
                raw += b"0"
            return bytes.fromhex(raw.decode("ascii"))
        if c == 0x3E:
            self.i += 2 if d[self.i:self.i + 2] == b">>" else 1
            return Op(">>")
        if c in b"[]{}":
            self.i += 1
            return Op(chr(c))
        if c == 0x2F:                                         # /Name with #xx escapes
            j = _REGULAR_RE.match(d, self.i + 1).end()
            raw = d[self.i + 1:j]
            self.i = j
            if b"#" in raw:
                raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
            return Name(raw.decode("latin-1"))
        j = _REGULAR_RE.match(d, self.i).end()
        if j == self.i:                                       # cannot happen: every delimiter is handled above
            self.i += 1
            return Op(chr(c))
        raw = d[self.i:j]
        self.i = j
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return Op(raw.decode("latin-1"))

    def _literal(self) -> bytes:
        d, n = self.d, self.n
        i, depth, out = self.i + 1, 1, bytearray()
        esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
        while i < n:
            m = _LITERAL_SPECIAL_RE.search(d, i)              # copy plain runs in one step
            k = m.start() if m else n
            out += d[i:k]
            i = k
            if i >= n:
                break
            c = d[i]
            if c == 0x5C and i + 1 < n:                       # backslash
                i += 1
                c = d[i]
                if c in esc:
                    out.append(esc[c])
                elif 0x30 <= c <= 0x37:
                    k = i
                    while k < n and k < i + 3 and 0x30 <= d[k] <= 0x37:
                        k += 1
                    out.append(int(d[i:k], 8) & 0xFF)
                    i = k - 1
                elif c == 0x0D:                               # a line break after a backslash joins the lines
                    if i + 1 < n and d[i + 1] == 0x0A:
                        i += 1
                elif c != 0x0A:
                    out.append(c)
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    i += 1
                    break
                out.append(c)
            else:
                out.append(c)
            i += 1
        self.i = i
        return bytes(out)


def parse_object(lx: Lexer, tok=None):
    """One PDF object. `a b R` becomes a Ref."""
    t = lx.token() if tok is None else tok
    if isinstance(t, Op):
        if t == "<<":
            out = {}
            while True:
                k = lx.token()
                if k is None or k == ">>":
                    return out
                if isinstance(k, Name):
                    out[str(k)] = parse_object(lx)
        if t == "[":
            out = []
            while True:
                k = lx.token()
                if k is None or k == "]":
                    return out
                out.append(parse_object(lx, k))
        if t in ("true", "false"):
            return t == "true"
        if t == "null":
            return None
        return t
    if isinstance(t, int) and not isinstance(t, bool):
        save = lx.i
        t2 = lx.token()
        if isinstance(t2, int):
            t3 = lx.token()
            if t3 == "R":
                return Ref((t, t2))
        lx.i = save
    return t


# ---- the file ----

class Pdf:
    def __init__(self, data: bytes):
        if not data.startswith(b"%PDF-"):
            raise PdfError("This file is not a PDF.")
        self.d = data
        self.objs: dict[int, object] = {}
        self.streams: dict[int, tuple[dict, bytes]] = {}
        self.inflated = 0
        for m in _OBJ_RE.finditer(data):
            self._read_at(int(m.group(1)), m.end())
        for num in list(self.streams):
            head, _raw = self.streams[num]
            if head.get("Type") == "ObjStm":
                self._read_objstm(num)
        if any(isinstance(v, dict) and "Encrypt" in v for v in self._trailers()):
            raise PdfError("This PDF is locked with a password. Save it again without a password, then upload it.")

    def _trailers(self):
        for m in re.finditer(rb"trailer\s*<<", self.d):
            try:
                yield parse_object(Lexer(self.d, m.end() - 2))
            except Exception:  # noqa: BLE001: a broken trailer is skipped
                continue
        for head, _raw in self.streams.values():
            if head.get("Type") == "XRef":
                yield head

    def _read_at(self, num: int, pos: int):
        lx = Lexer(self.d, pos)
        try:
            obj = parse_object(lx)
        except Exception:  # noqa: BLE001: one broken object must not stop the rest
            return
        self.objs[num] = obj
        if isinstance(obj, dict):
            lx._skip()
            if self.d.startswith(b"stream", lx.i):
                start = lx.i + 6
                if self.d.startswith(b"\r\n", start):
                    start += 2
                elif self.d[start:start + 1] in (b"\n", b"\r"):
                    start += 1
                length = obj.get("Length")
                end = -1
                if isinstance(length, int) and 0 <= length and b"endstream" in self.d[start + length:start + length + 32]:
                    end = start + length
                else:
                    end = self.d.find(b"endstream", start)
                    if end < 0:
                        return
                    while end > start and self.d[end - 1] in b"\r\n":
                        end -= 1
                self.streams[num] = (obj, self.d[start:end])

    def _read_objstm(self, num: int):
        head, _raw = self.streams[num]
        data = self.stream_data(num)
        n, first = head.get("N"), head.get("First")
        if not isinstance(n, int) or not isinstance(first, int):
            return
        lx = Lexer(data)
        pairs = []
        for _ in range(min(n, 100_000)):
            a, b = lx.token(), lx.token()
            if not isinstance(a, int) or not isinstance(b, int):
                break
            pairs.append((a, b))
        for objnum, off in pairs:
            if objnum in self.objs:
                continue
            try:
                self.objs[objnum] = parse_object(Lexer(data, first + off))
            except Exception:  # noqa: BLE001
                continue

    def get(self, v, depth: int = 0):
        while isinstance(v, Ref) and depth < 32:
            v = self.objs.get(v[0])
            depth += 1
        return v

    def stream_data(self, ref_or_num) -> bytes:
        num = ref_or_num[0] if isinstance(ref_or_num, Ref) else ref_or_num
        if num not in self.streams:
            return b""
        head, raw = self.streams[num]
        filters = self.get(head.get("Filter"))
        filters = [filters] if isinstance(filters, str) else (filters or [])
        data = raw
        for f in filters:
            f = str(self.get(f))
            if f in ("FlateDecode", "Fl"):
                data = self._inflate(data)
            elif f in ("ASCIIHexDecode", "AHx"):
                hexd = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
                data = bytes.fromhex((hexd + b"0" * (len(hexd) % 2)).decode("ascii"))
            elif f in ("ASCII85Decode", "A85"):
                body = data.strip()
                body = body[2:] if body.startswith(b"<~") else body
                body = body.split(b"~>")[0]
                try:
                    data = base64.a85decode(re.sub(rb"\s", b"", body))
                except ValueError:
                    return b""
            else:                                             # images and other filters: no text inside
                return b""
        return data

    def _inflate(self, data: bytes) -> bytes:
        room = MAX_INFLATE - self.inflated
        if room <= 0:
            raise PdfError("This PDF is too large to read.")
        z = zlib.decompressobj()
        try:
            out = z.decompress(data, room)
        except zlib.error:
            out = b""
            try:                                              # a broken end still gives the start
                z = zlib.decompressobj()
                for k in range(0, len(data), 1024):
                    out += z.decompress(data[k:k + 1024], max(room - len(out), 1))
            except zlib.error:
                pass
        if z.unconsumed_tail:
            raise PdfError("This PDF is too large to read.")
        self.inflated += len(out)
        return out

    # ---- pages in reading order ----
    def pages(self) -> list[tuple[dict, dict]]:
        """(page, resources) for each page. Resources are inherited from the parent nodes."""
        out: list[tuple[dict, dict]] = []
        root = None
        for t in self._trailers():
            r = self.get(t.get("Root")) if isinstance(t, dict) else None
            if isinstance(r, dict):
                root = r
        if root is None:
            root = next((o for o in self.objs.values() if isinstance(o, dict) and o.get("Type") == "Catalog"), None)
        seen: set[int] = set()

        def walk(node_ref, inherited, depth):
            node = self.get(node_ref)
            if not isinstance(node, dict) or depth > 32 or len(out) > 500:
                return
            if isinstance(node_ref, Ref):
                if node_ref[0] in seen:
                    return
                seen.add(node_ref[0])
            res = self.get(node.get("Resources"))
            res = res if isinstance(res, dict) else inherited
            kids = self.get(node.get("Kids"))
            if node.get("Type") == "Page" or (kids is None and "Contents" in node):
                out.append((node, res or {}))
            elif isinstance(kids, list):
                for k in kids:
                    walk(k, res, depth + 1)

        if isinstance(root, dict):
            walk(root.get("Pages"), {}, 0)
        if not out:                                           # no page tree: every page object in number order
            for num in sorted(self.objs):
                o = self.objs[num]
                if isinstance(o, dict) and o.get("Type") == "Page":
                    res = self.get(o.get("Resources"))
                    out.append((o, res if isinstance(res, dict) else {}))
        return out


# ---- fonts ----

class Font:
    def __init__(self, cmap: dict[bytes, str], widths: set[int], codec: str = "cp1252",
                 glyph_w: dict[int, float] | None = None, default_w: float = 0.0, code_len: int = 1):
        self.cmap, self.widths, self.codec = cmap, sorted(widths, reverse=True) or [1], codec
        self.glyph_w, self.default_w, self.code_len = glyph_w, default_w, code_len   # glyph widths in 1/1000 em, or None

    def advance(self, s: bytes) -> float | None:
        """How far the pen moves for these bytes, in em (1.0 = the font size). None when the font gives no widths."""
        if self.glyph_w is None:
            return None
        n = self.code_len
        return sum(self.glyph_w.get(int.from_bytes(s[i:i + n], "big"), self.default_w) for i in range(0, len(s), n)) / 1000.0

    def decode(self, s: bytes) -> str:
        if not self.cmap:
            if self.widths == [2]:                            # two-byte codes with no map: no readable text
                return ""
            return s.decode(self.codec, "replace")
        out, i = [], 0
        while i < len(s):
            for w in self.widths:
                code = s[i:i + w]
                if code in self.cmap:
                    out.append(self.cmap[code])
                    i += w
                    break
            else:
                w = self.widths[-1]
                if w == 1:
                    out.append(s[i:i + 1].decode("cp1252", "replace"))
                i += w
        return "".join(out)


def _utf16(b: bytes) -> str:
    try:
        return b.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def parse_cmap(data: bytes) -> tuple[dict[bytes, str], set[int]]:
    cmap: dict[bytes, str] = {}
    widths: set[int] = set()
    for block in re.findall(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
        for lo in re.findall(rb"<([0-9A-Fa-f]+)>\s*<[0-9A-Fa-f]+>", block):
            widths.add(max(1, len(lo) // 2))
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            cmap[bytes.fromhex(src.decode())] = _utf16(bytes.fromhex(dst.decode()[:len(dst) // 2 * 2]))
            widths.add(len(src) // 2)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            w = len(lo) // 2
            a, b = int(lo, 16), int(hi, 16)
            if b < a or b - a > 65535:
                continue
            widths.add(w)
            if rest.startswith(b"["):
                dsts = re.findall(rb"<([0-9A-Fa-f]*)>", rest)
                for k, dst in enumerate(dsts[:b - a + 1]):
                    cmap[(a + k).to_bytes(w, "big")] = _utf16(bytes.fromhex(dst.decode()[:len(dst) // 2 * 2]))
            else:
                base = bytes.fromhex(rest[1:-1].decode()[:(len(rest) - 2) // 2 * 2])
                if not base:
                    continue
                last = int.from_bytes(base[-2:] if len(base) >= 2 else base, "big")
                for k in range(b - a + 1):
                    nb = base[:-2] + ((last + k) & 0xFFFF).to_bytes(2, "big") if len(base) >= 2 else bytes([(last + k) & 0xFF])
                    cmap[(a + k).to_bytes(w, "big")] = _utf16(nb)
    return cmap, widths


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def read_widths(pdf: "Pdf", fd: dict) -> tuple[dict[int, float] | None, float, int]:
    """(glyph widths, default width, bytes per code) from /Widths (simple fonts) or /W and /DW (two-byte fonts)."""
    try:
        if fd.get("Subtype") == "Type0":
            kids = pdf.get(fd.get("DescendantFonts"))
            df = pdf.get(kids[0]) if isinstance(kids, list) and kids else None
            if not isinstance(df, dict):
                return None, 0.0, 2
            dw = _num(pdf.get(df.get("DW")))
            gw: dict[int, float] = {}
            w = pdf.get(df.get("W"))
            w = [pdf.get(x) for x in w] if isinstance(w, list) else []
            i = 0
            while i < len(w) and len(gw) < 70_000:
                a = _num(w[i])
                nxt = w[i + 1] if i + 1 < len(w) else None
                if a is not None and isinstance(nxt, list):
                    for k, x in enumerate(nxt):
                        if _num(x) is not None:
                            gw[int(a) + k] = float(x)
                    i += 2
                elif a is not None and _num(nxt) is not None and i + 2 < len(w) and _num(w[i + 2]) is not None:
                    for c in range(int(a), min(int(nxt), int(a) + 65535) + 1):
                        gw[c] = float(w[i + 2])
                    i += 3
                else:
                    i += 1
            return gw, float(dw if dw is not None else 1000), 2
        first, ws = _num(pdf.get(fd.get("FirstChar"))), pdf.get(fd.get("Widths"))
        if first is None or not isinstance(ws, list):
            return None, 0.0, 1
        gw = {int(first) + k: float(_num(pdf.get(x)) or 0) for k, x in enumerate(ws[:256])}
        return gw, 0.0, 1
    except Exception:  # noqa: BLE001: bad widths only mean the reader guesses the spaces
        return None, 0.0, 1


# ---- content streams ----

def _show(parts: list[str], state: dict, text: str):
    if not text:
        return
    if state["pending"] and parts:
        parts.append(state["pending"])
    state["pending"] = ""
    parts.append(text)


def _wide_gap(dx, state: dict) -> bool:
    """Is the move `dx` along the line a word space? Only the empty room between glyphs counts, not the glyphs themselves.
    Letter-spaced and one-glyph-per-move PDFs (Canva, Figma) put a move between every two letters, so a move alone is no space."""
    if dx is None:
        return True                                           # no distance known: a break, as before
    size, adv = state["size"], state["adv"]
    if adv is not None:
        return dx - adv * size > 0.2 * size                   # more than a fifth of an em of room
    return dx > 0.75 * size                                   # no glyph widths: a move wider than most letters


def _run(pdf: Pdf, data: bytes, res: dict, parts: list[str], depth: int, fonts_cache: dict):
    fonts = pdf.get(res.get("Font")) or {}
    xobjs = pdf.get(res.get("XObject")) or {}
    lx = Lexer(data)
    stack: list = []
    font: Font | None = None
    state = {"pending": "", "y": None, "size": 12.0, "adv": None}   # adv: pen move of the last shown text, in text-space units

    def font_for(name):
        ref = fonts.get(str(name)) if isinstance(fonts, dict) else None
        key = ref[0] if isinstance(ref, Ref) else id(ref)
        if key not in fonts_cache:
            fd = pdf.get(ref)
            cmap, widths, codec = {}, set(), "cp1252"
            gw, dw, clen = None, 0.0, 1
            if isinstance(fd, dict):
                gw, dw, clen = read_widths(pdf, fd)
                tu = fd.get("ToUnicode")
                if isinstance(tu, Ref):
                    cmap, widths = parse_cmap(pdf.stream_data(tu))
                if not cmap and fd.get("Subtype") == "Type0":
                    cmap, widths = {}, {2}
                enc = pdf.get(fd.get("Encoding"))
                base = enc.get("BaseEncoding") if isinstance(enc, dict) else enc
                if base == "MacRomanEncoding":
                    codec = "mac_roman"
            fonts_cache[key] = Font(cmap, widths or {1}, codec, gw, dw, clen)
        return fonts_cache[key]

    def move(y, dx=None):
        if state["y"] is not None and y is not None and abs(y - state["y"]) > 1:
            state["pending"] = "\n"
        elif not state["pending"]:
            state["pending"] = " " if _wide_gap(dx, state) else ""
        if y is not None:
            state["y"] = y
        state["adv"] = None

    while True:
        t = lx.token()
        if t is None:
            break
        if not isinstance(t, Op):
            stack.append(t)
            continue
        if t in ("[", "<<"):
            stack.append(parse_object(lx, t))
            continue
        if t == "BI":                                         # inline image: skip its bytes
            k = re.compile(rb"\sID\s").search(lx.d, lx.i)
            e = re.compile(rb"\sEI(?=[\s]|$)").search(lx.d, k.end() if k else lx.i)
            lx.i = e.end() if e else lx.n
            stack.clear()
            continue
        if t == "Tf" and len(stack) >= 2:
            font = font_for(stack[-2])
            if isinstance(stack[-1], (int, float)) and stack[-1] > 0:
                state["size"] = float(stack[-1])
        elif t in ("Tj", "'", '"') and stack and isinstance(stack[-1], bytes):
            if t != "Tj":
                state["pending"] = "\n"
            _show(parts, state, (font or Font({}, {1})).decode(stack[-1]))
            state["adv"] = (font.advance(stack[-1]) if font else None)
        elif t == "TJ" and stack and isinstance(stack[-1], list):
            for item in stack[-1]:
                if isinstance(item, bytes):
                    _show(parts, state, (font or Font({}, {1})).decode(item))
                elif isinstance(item, (int, float)) and item < -180 and not state["pending"]:
                    state["pending"] = " "
            state["adv"] = None
        elif t in ("Td", "TD") and len(stack) >= 2 and all(isinstance(x, (int, float)) for x in stack[-2:]):
            ty = stack[-1]
            if abs(ty) > 1:
                state["pending"] = "\n"
                state["y"] = (state["y"] or 0) + ty
            else:
                move(None, stack[-2])
        elif t == "Tm" and len(stack) >= 6 and isinstance(stack[-1], (int, float)):
            move(stack[-1])
        elif t == "T*":
            state["pending"] = "\n"
        elif t == "ET":
            if not state["pending"]:
                state["pending"] = " "
        elif t == "Do" and stack and isinstance(stack[-1], Name) and depth < MAX_FORM_DEPTH and isinstance(xobjs, dict):
            ref = xobjs.get(str(stack[-1]))
            if isinstance(ref, Ref) and ref[0] in pdf.streams and pdf.streams[ref[0]][0].get("Subtype") == "Form":
                head = pdf.streams[ref[0]][0]
                sub = pdf.get(head.get("Resources"))
                parts.append("\n")
                _run(pdf, pdf.stream_data(ref), sub if isinstance(sub, dict) else res, parts, depth + 1, fonts_cache)
                parts.append("\n")
        stack.clear()
        if len(parts) > MAX_CHARS // 2:                       # each part is at least one character
            break


def collapse_letter_runs(line: str) -> str:
    """Fallback for a line that is mostly single characters split by single spaces ("A B C  D E"): join them into words.
    Two or more spaces stay a word break. A normal line, with a few one-letter words, is not touched."""
    tokens = line.split(" ")
    real = [t for t in tokens if t]
    if len(real) < 6 or sum(1 for t in real if len(t) == 1) < 0.8 * len(real):
        return line
    return " ".join(w.replace(" ", "") for w in re.split(r" {2,}", line))


def extract_text(data: bytes) -> str:
    """The text of every page, in order, one line per text line. PdfError for a file that is not a readable PDF."""
    pdf = Pdf(data)
    pages = pdf.pages()
    if not pages:
        raise PdfError("This PDF has no pages Friday can read.")
    parts: list[str] = []
    fonts_cache: dict = {}
    chars = 0
    for page, res in pages:
        contents = pdf.get(page.get("Contents"))
        refs = contents if isinstance(contents, list) else [page.get("Contents")]
        data_ = b"\n".join(pdf.stream_data(r) for r in refs if isinstance(r, Ref))
        start = len(parts)
        _run(pdf, data_, res, parts, 0, fonts_cache)
        parts.append("\n\n")
        chars += sum(len(p) for p in parts[start:])
        if chars > MAX_CHARS:
            break
    text = unicodedata.normalize("NFKC", "".join(parts))[:MAX_CHARS]
    text = "\n".join(collapse_letter_runs(ln) for ln in text.replace("\r", "\n").split("\n"))
    text = re.sub(r"[^\S\n]+", " ", text)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return "\n".join(line.strip() for line in text.split("\n")).strip()
