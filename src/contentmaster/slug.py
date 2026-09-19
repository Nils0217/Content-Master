"""One shared "make this string safe for a filename" function.

2026-09-17 (code scan finding): `topic_index._slug()`,
`pipeline._dataset_slug()`, and `modiqo_play._play_key()` each had their
own version of this — the first two were identical regex-based
sanitization, the third (used to build `plays/*.json` snapshot filenames)
was weaker: it only lowercased and replaced spaces, so any other special
character (e.g. a comma — a real product name used this session was
"Hairy, furry, cute little monster") passed straight into a file path
unsanitized. Consolidated into one function all three now call. Verified
safe to change `_play_key()`'s behavior before doing so: zero
`plays/*.json` snapshot files existed at the time (nothing to orphan by
changing how the name is built).
"""
from __future__ import annotations

import re


def slugify(text: str, default: str = "item") -> str:
    """Lowercases, replaces any run of non-alphanumeric characters with a
    single '-', strips leading/trailing '-'. Falls back to `default` if
    that leaves nothing (e.g. `text` was empty or pure punctuation).
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or default
