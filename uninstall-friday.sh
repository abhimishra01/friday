#!/usr/bin/env bash
# Remove Friday from this computer. It asks before it deletes your data.
set -u
APP="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s)" in
  Darwin) DONNA_HOME="${DONNA_HOME:-$HOME/Library/Application Support/Friday}"; CFG="$HOME/.config/friday";;
  *)      DONNA_HOME="${DONNA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/friday}"; CFG="${XDG_CONFIG_HOME:-$HOME/.config}/friday";;
esac
echo "This removes Friday's private Python folder."
rm -rf "$DONNA_HOME/venv" "$DONNA_HOME/friday.log"
echo ""
echo "Your data (your CV, jobs, drafts, settings) is in: $DONNA_HOME/data"
echo "Your Gmail login is in: $CFG"
printf 'Delete your data and your Gmail login too? This cannot be undone. Type yes to delete, or press Enter to keep them: '
read -r answer
if [ "$answer" = "yes" ]; then rm -rf "$DONNA_HOME" "$CFG"; echo "Your data and login are deleted."; else echo "Your data and login are kept."; fi
echo ""
echo "Last step: delete this folder: $APP"
