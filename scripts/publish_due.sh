#!/bin/sh
# What the Mac runs at posting time (launchd, see src/contentmaster/scheduler.py).
#
# 2026-10-25: Homebrew's python@3.14 disappeared from this machine and the
# project's .venv — which links to it — stopped working without a sound. A
# scheduled post would simply not have gone out. This checks the
# environment first and, if it is broken, says so on screen instead.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/publish.log"
mkdir -p "$ROOT/logs"
if ! "$ROOT/.venv/bin/python" -c 'import contentmaster' >/dev/null 2>&1; then
  MSG="Scheduled publish could not run: the Python environment is broken. Fix: brew install python@3.14"
  echo "$(date '+%Y-%m-%d %H:%M:%S') [publish-due] $MSG" >> "$LOG"
  "${CONTENTMASTER_NOTIFY:-/usr/bin/osascript}" -e "display notification \"$MSG\" with title \"ContentMaster\" sound name \"Basso\""
  exit 1
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') [publish-due] start" >> "$LOG"
exec "$ROOT/.venv/bin/contentmaster" publish-due
