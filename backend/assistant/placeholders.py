"""RB3: text that still shows a template. the mail module never sends an email or a website that does. One list of patterns for
both, so the mail check and the site check cannot disagree."""
from __future__ import annotations

import re

# Case does not matter: {{name}}, <<date>>, [placeholder], [Name], (replace with ...), lorem ipsum, [anything...].
_LOOSE = re.compile(
    r"\{\{|\}\}|<<[^>\n]{0,40}>>"
    r"|\[(?:placeholder|insert[^\]\n]{0,40}|your [^\]\n]{0,30}|(?:first ?|last |full |contact |company )?name|date|time|day|link|url|tbd|xx+)\]"
    r"|\[[^\]\n]{0,60}(?:\.\.\.|…)\]"
    r"|\((?:please )?(?:replace|insert|add your|fill in|your text here|todo|tbd)\b[^)\n]{0,80}\)"
    r"|\blorem ipsum\b", re.I)
# Case matters: a bracket in capitals is a slot: [N], [X%], [PROJECT NAME], [TESTIMONIAL: add a quote].
_CAPS = re.compile(r"\[(?=[^\]\n]*[A-Z])[A-Z0-9][A-Z0-9 %#/&'\-.:,]{0,60}\]|\[[A-Z]{3,}\b[^\]\n]{0,60}\]")

class _Both:
    """`PLACEHOLDER_RE.search(text)` keeps working for old callers, with the exact rules of find()."""
    @staticmethod
    def search(text: str):
        for rx in (_CAPS, _LOOSE):
            m = rx.search(text or "")
            if m:
                return m
        return None


def find(text: str) -> list[str]:
    """The placeholders in the text, each once, in order of the pattern (at most 8)."""
    out: list[str] = []
    for rx in (_CAPS, _LOOSE):
        for m in rx.finditer(text or ""):
            if m.group(0) not in out:
                out.append(m.group(0))
    return out[:8]


PLACEHOLDER_RE = _Both()
