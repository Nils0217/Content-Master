#!/usr/bin/env bash
# Runs every suite in tests/ against a fresh scratch directory.
# Fails loudly if a file is missing rather than quietly running fewer.
set -uo pipefail
cd "$(dirname "$0")/.."
SCRATCH="${1:-/tmp/contentmaster-tests}"
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
fail=0; count=0
for f in tests/test_*.py; do
    [ -e "$f" ] || { echo "no test files found in tests/"; exit 1; }
    count=$((count+1))
    name=$(basename "$f" .py)
    out=$(./.venv/bin/python "$f" "$SCRATCH/$name" 2>&1)
    if printf '%s' "$out" | tail -1 | grep -q "all passed"; then
        printf '  %-34s ok\n' "$name"
    else
        printf '  %-34s FAILED\n' "$name"
        printf '%s\n' "$out" | tail -12 | sed 's/^/      /'
        fail=1
    fi
done
echo
echo "$count suite(s); $([ $fail -eq 0 ] && echo 'all passed' || echo 'FAILURES above')"
exit $fail
