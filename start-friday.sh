#!/usr/bin/env bash
# Start Friday on macOS or Linux. Safe to run again: it reuses everything and opens the page.
set -u
APP="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s)" in
  Darwin) DONNA_HOME="${DONNA_HOME:-$HOME/Library/Application Support/Friday}";;
  *)      DONNA_HOME="${DONNA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/donna}";;
esac
export DONNA_HOME
export DONNA_DATA_DIR="${DONNA_DATA_DIR:-$DONNA_HOME/data}"
mkdir -p "$DONNA_HOME" "$DONNA_DATA_DIR"

say() { printf '%s\n' "$*"; }
stop_here() { say ""; say "$*"; say ""; [ "${DONNA_NO_PAUSE:-}" = "1" ] || { printf 'Press Enter to close this window. '; read -r _; }; exit 1; }

say "Starting Friday..."

# 1. Python 3.11 to 3.13
PY="${DONNA_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
  done
fi
[ -n "$PY" ] || stop_here "Friday needs Python 3.11, 3.12 or 3.13, and cannot find one.
Install it from https://www.python.org/downloads/ and start Friday again."

# 2. Claude Code
command -v claude >/dev/null 2>&1 || stop_here "Friday uses your own Claude Code, and cannot find the 'claude' program.
Install it from https://docs.claude.com/en/docs/claude-code , open a terminal, type: claude , sign in, then start Friday again."
if [ "${DONNA_SKIP_CLAUDE_CHECK:-}" != "1" ]; then
  say "Checking that Claude answers (this uses a few words of your Claude usage)..."
  out="$(claude -p "Reply with the single word: ready" </dev/null 2>/dev/null)"
  [ -n "$out" ] || stop_here "Claude did not answer. Open a terminal, type: claude , sign in, then start Friday again."
fi

# 3. Private Python folder with Friday's packages (made once; again when the list changes)
if [ -z "${DONNA_NO_VENV:-}" ]; then
  VENV="$DONNA_HOME/venv"
  STAMP="$VENV/.requirements-stamp"
  WANT="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$APP/requirements.txt")"
  if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
    say "Installing Friday's packages (first start only, about a minute)..."
    "$PY" -m venv "$VENV" || stop_here "Could not make Friday's private Python folder."
    # shellcheck disable=SC2086
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check ${DONNA_PIP_ARGS:-} -r "$APP/requirements.txt" || stop_here "Could not install the packages. Check your internet connection and start Friday again."
    printf '%s' "$WANT" > "$STAMP"
  fi
  PY="$VENV/bin/python"
fi

# 4. A free local port (8795 first), on this computer only
PORT="$("$PY" -c '
import socket
start = int(__import__("os").environ.get("DONNA_PORT", "8795"))
for p in range(start, start + 100):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p)); s.close(); print(p); break
    except OSError:
        s.close()
')"
BASE="${DONNA_PORT:-8795}"
if "$PY" -c 'import sys,urllib.request,json; r=json.load(urllib.request.urlopen("http://127.0.0.1:%s/api/health"%sys.argv[1],timeout=2)); sys.exit(0 if r.get("app")=="friday" else 1)' "$BASE" 2>/dev/null; then
  PORT="$BASE"; say "Friday is already running."; RUNNING=1
else RUNNING=0; fi
[ -n "$PORT" ] || stop_here "Friday found no free local port. Close other programs and start Friday again."
URL="http://127.0.0.1:$PORT/"

if [ "$RUNNING" = "0" ]; then
  export DONNA_PORT="$PORT" DONNA_FRONTEND_DIR="$APP/frontend"
  cd "$APP" || exit 1
  "$PY" -m backend.donna_app >>"$DONNA_HOME/friday.log" 2>&1 &
  PID=$!
  trap 'kill $PID 2>/dev/null' EXIT INT TERM
  for _ in $(seq 1 60); do
    "$PY" -c 'import sys,urllib.request; urllib.request.urlopen("http://127.0.0.1:%s/api/health"%sys.argv[1],timeout=1)' "$PORT" 2>/dev/null && break
    kill -0 $PID 2>/dev/null || stop_here "Friday stopped at start. The log is in: $DONNA_HOME/friday.log"
    sleep 1
  done
fi

say "Friday is ready at $URL"
if [ -z "${DONNA_NO_BROWSER:-}" ]; then
  if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1; fi
fi
[ "$RUNNING" = "1" ] && exit 0
say "Keep this window open while Friday works. Close it to stop her."
wait $PID
