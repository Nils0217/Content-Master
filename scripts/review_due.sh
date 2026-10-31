#!/bin/sh
# What the Mac runs once a day for `contentmaster review --schedule`
# (launchd, see src/contentmaster/scheduler.py). Same environment check as
# publish_due.sh: if the venv is broken, say so on screen instead of silently
# never analysing anything.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/review.log"
mkdir -p "$ROOT/logs"
if ! "$ROOT/.venv/bin/python" -c 'import contentmaster' >/dev/null 2>&1; then
  MSG="Scheduled review could not run: the Python environment is broken. Fix: brew install python@3.14"
  echo "$(date '+%Y-%m-%d %H:%M:%S') [review-due] $MSG" >> "$LOG"
  "${CONTENTMASTER_NOTIFY:-/usr/bin/osascript}" -e "display notification \"$MSG\" with title \"ContentMaster\" sound name \"Basso\""
  exit 1
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') [review-due] start" >> "$LOG"
exec "$ROOT/.venv/bin/contentmaster" review-due
