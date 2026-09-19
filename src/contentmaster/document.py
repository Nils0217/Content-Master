"""Read a source document's plain text locally — no Cognee, no network.

2026-09-19 (code scan): draft_generator.draft_posts() has always
documented a "no topic index yet (Cognee down, or nothing extracted)"
fallback that generates from a plain text blob instead. That fallback was
dead code: the 2026-09-15 refactor deleted step1_cognee_extract(), the
only thing that ever produced such a blob, but left the parameter and its
branch in place. So a Cognee outage skipped the LLM entirely and dropped
straight to a hardcoded template string — every draft identical, run
after run, with a healthy Ollama sitting right there unused.

This module is that blob's new source. Deliberately local-only: the whole
point is to still work when Cognee (or the network) is not there.

Format support is best-effort by design — an unreadable format returns ""
rather than raising, and the caller falls back one more level. .rtf reuses
`striprtf`, already a dependency for cognee_client's own .rtf handling.
PDF needs `pypdf`, which is NOT a dependency of this project: if it is not
installed, PDFs simply return "" here and nothing breaks. Install it
(`pip install pypdf`) if you want the fallback to cover PDF whitepapers
too.
"""
from __future__ import annotations

from pathlib import Path

# Default truncation for the *prompt* path only — small local models
# degrade badly on very long contexts, and the draft prompt only needs
# enough grounding to write a few short posts. Callers that need the whole
# document (Cognee ingestion) pass max_chars=None.
PROMPT_MAX_CHARS = 6000


def read_text(path: str | Path, max_chars: int | None = None) -> str:
    """Best-effort plain text for `path`. Returns "" for anything that
    cannot be read locally (missing file, unsupported format, missing
    optional dependency) — never raises, so a caller can treat "" as
    "no grounding available" and degrade one more level.

    `max_chars` truncates the result; None (the default) returns the whole
    document. Ingestion callers must NOT truncate — cognee_client feeds
    this straight into the knowledge graph, and silently dropping the tail
    of a whitepaper there would quietly shrink everything downstream.
    """
    path = Path(path)
    try:
        if not path.is_file():
            return ""
        suffix = path.suffix.lower()

        if suffix == ".rtf":
            from striprtf.striprtf import rtf_to_text

            text = rtf_to_text(path.read_text(errors="replace"))
        elif suffix == ".pdf":
            text = _read_pdf(path)
        else:
            # .txt/.md/.markdown and anything else that is plausibly plain
            # text. errors="replace" keeps a stray byte from failing the
            # whole read.
            text = path.read_text(errors="replace")
    except Exception:  # noqa: BLE001 — a fallback that can raise is not a fallback
        return ""

    text = text.strip()
    return text[:max_chars] if max_chars is not None else text


def _read_pdf(path: Path) -> str:
    """Optional — returns "" when `pypdf` is not installed (see module
    docstring; it is deliberately not a hard dependency).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)
