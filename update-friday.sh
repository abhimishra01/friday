#!/usr/bin/env bash
# Update Friday. Your data is never touched: it lives outside this folder.
set -u
APP="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$APP/.git" ]; then
  echo "Getting the newest Friday..."
  git -C "$APP" pull --ff-only || { echo "The update failed. Your data is safe. Try again later."; exit 1; }
  echo "Updated. Start Friday now."
  exec "$APP/start-friday.sh"
fi
cat <<'T'
To update Friday from a zip:
  1. Download the newest Friday zip and unzip it.
  2. Start Friday from the new folder (Start Friday).
Your data stays: it is kept outside the Friday folder. You can delete the old folder afterwards.
T
