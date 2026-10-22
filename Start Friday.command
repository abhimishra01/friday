#!/usr/bin/env bash
# Double-click on macOS. Opens Terminal and starts Friday.
cd "$(dirname "$0")" || exit 1
exec ./start-friday.sh
