"""Running Claude for Friday: one sealed `claude -p` call with no tools, and the small text helpers around it."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..sealed import SEALED_ARGS

SONNET, OPUS = "claude-sonnet-5-5", "claude-opus-5-5"


# Phrases that try to give the mail module orders or to get something out of her. Checked in code on every inbound email.
INJECTION = [
    r"ignore (?:all |any |your |the |these |my )*(?:previous |prior |above |earlier )*(?:rules|instructions|guidelines|prompt|restrictions|limits)",
    r"disregard (?:all |any |your |the |previous |prior )*(?:rules|instructions|guidelines|prompt)",
    r"forget (?:all |your |the |previous )*(?:rules|instructions)",
    r"(?:reveal|show|tell|give|share|print|leak|send) (?:me )?(?:your |the )?(?:system prompt|instructions|password|app password|api key|credentials|secret|settings|allow[- ]?list|contacts list)",
    r"you are now\b", r"new (?:rules|instructions) for you", r"\bact as (?:the owner|an admin)",
    r"(?:turn|switch) off (?:your |the )?(?:safety|rules|kill switch|filters)",
    r"change (?:your |the )?(?:settings|rules|allow[- ]?list|password)",
    r"add (?:me|this address|this email|them|him|her) to (?:your |the )?(?:allow|approved|contacts)",
    r"\b(?:forward|send|email|mail|cc|bcc|copy|share|write)\b[^.\n]{0,60}\b(?:this|it|that|everything|the (?:thread|files?|research|details))\b[^.\n]{0,40}\bto\b(?!\s+(?:you|me|us)\b)",
]


INJECTION_RE = [re.compile(p, re.I) for p in INJECTION]


def strip_quotes(body: str) -> str:
    """Only the new text of an email: no '>' quote lines and nothing after 'On ... wrote:'."""
    out = []
    for line in (body or "").splitlines():
        if re.match(r"\s*On .{5,200}wrote:\s*$", line) or re.match(r"\s*-{2,}\s*Original Message", line, re.I):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out)


def fence(text: str, nonce: str) -> str:
    return (text or "").replace(nonce, "")


SYSTEM = ("You are a text-only assistant. Follow only the task in the user message. "
          "You have no tools, no files and no memory. Return only what the task asks for.")


def run_claude(claude_bin: str, prompt: str, model: str, effort: str, cwd: Path | None = None,
               timeout: int = 600, run_root: Path | None = None) -> str:
    """`claude -p` with no tools, no MCP servers and the default Claude Code system prompt replaced. It runs in a new
    empty folder outside the repo and outside ~/Desktop, so no project CLAUDE.md or project memory is found (PA1R H1).
    `cwd` is ignored on purpose. `--setting-sources ""` also stops the user-level ~/.claude/CLAUDE.md from loading
    (PA1R H1, closed by SIM5; checked with a canary phrase).
    The prompt goes in on stdin. `run_root` (the settings' run folder) holds the new empty folder; the sandbox sets it."""
    argv = [claude_bin, "-p", "--model", model, "--effort", effort, "--tools", "", *SEALED_ARGS,
            "--disable-slash-commands", "--no-session-persistence", "--system-prompt", SYSTEM,
            "--output-format", "text"]
    root = Path(run_root) if run_root else Path.home() / ".config" / "donna" / "run"
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    try:
        r = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"Claude stopped with code {r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout
