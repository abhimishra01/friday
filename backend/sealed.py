"""The flags every sandbox or simulation `claude` run carries (night-run rules, COMMON.md): no MCP servers at all, and
no user, project or local settings (so no ~/.claude/CLAUDE.md, hooks or allow rules). One place, so a test can check them."""
from __future__ import annotations

SEALED_MCP = '{"mcpServers":{}}'
# Flags for a command line (`claude -p ...`).
SEALED_ARGS = ["--strict-mcp-config", "--mcp-config", SEALED_MCP, "--setting-sources", ""]
SEALED_ARGS_EQ = ["--strict-mcp-config", "--mcp-config", SEALED_MCP, "--setting-sources="]   # the form the Agent SDK writes


def is_sealed(argv: list) -> bool:
    """True when the command line has all three flags."""
    a = [str(x) for x in argv]
    if "--strict-mcp-config" not in a or "--mcp-config" not in a:
        return False
    i = a.index("--mcp-config")
    if i + 1 >= len(a) or a[i + 1].replace(" ", "") != SEALED_MCP:
        return False
    if "--setting-sources=" in a:
        return True
    return "--setting-sources" in a and a.index("--setting-sources") + 1 < len(a) and a[a.index("--setting-sources") + 1] == ""
