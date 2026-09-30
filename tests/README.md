# Tests

Run one:

    ./.venv/bin/python tests/test_scoring_gates.py /tmp/cm-test

Run all:

    ./scripts/run_tests.sh

Each file takes a scratch directory as its only argument, sets
`CONTENTMASTER_DATA_ROOT` to it, and exits non-zero on the first failed
assertion. Nothing here touches the real ledger.

These lived in a temp directory until 2026-09-26 and kept disappearing —
three suites were lost and had to be rewritten from memory, and a run
reporting "all passed" for eleven files said nothing about the three that
were no longer there. A test you cannot rely on being present is not
coverage.
