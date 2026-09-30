"""Where the source documents and the external data live.

2026-09-26. Two paths were being carried in two different ways, neither of
them chosen once and remembered:

* the whitepaper was `--whitepaper "Demo-white paper/cat.rtfd"`, retyped on
  every run. A stray trailing space in it once made the whole document
  unreadable, and the run continued against a stale cached index rather
  than stopping.
* the external data (Google Trends exports) was `warehouse/seeds/`,
  hardcoded in dbt_project.yml, findable only by reading that file.

Both are workspace choices — they change when you start working on a
different product, not between runs — so they are set once and stored.

The whitepaper folder also decides the Cognee dataset (see
pipeline.dataset_for), which is the other reason it belongs here rather
than in a flag: a folder is a thing that exists and that nobody retypes,
so it cannot be mistyped into a new empty knowledge graph the way a
product name could.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import settings

CONFIG_PATH = settings.data_root / "plays" / "_folders.json"

DEFAULTS: dict[str, str] = {
    "whitepapers": "Demo-white paper",
    "external": "warehouse/seeds",
}

# What counts as a source document. Directories ending .rtfd are macOS
# bundles and are documents too (see document.resolve_bundle).
DOCUMENT_SUFFIXES = (".rtf", ".rtfd", ".pdf", ".txt", ".md", ".markdown", ".docx")
EXTERNAL_SUFFIXES = (".csv", ".tsv", ".json", ".jsonl")


def _load() -> dict[str, str]:
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            stored = {}
    else:
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if isinstance(v, str)}}


def get(kind: str) -> Path:
    """The configured folder, resolved against the project root."""
    raw = _load().get(kind, DEFAULTS.get(kind, ""))
    path = Path(raw).expanduser()
    return path if path.is_absolute() else settings.project_root / path


def set_folder(kind: str, value: str) -> Path:
    """Point `kind` at a folder. Raises if it is not a directory —
    accepting a path that does not exist is how a run ends up quietly
    reading nothing.
    """
    if kind not in DEFAULTS:
        raise ValueError(f"Unknown folder {kind!r}; expected one of {', '.join(DEFAULTS)}.")
    path = Path(value).expanduser()
    resolved = path if path.is_absolute() else settings.project_root / path
    if not resolved.is_dir():
        raise NotADirectoryError(f"{resolved} is not a folder that exists.")
    stored = _load()
    try:
        stored[kind] = str(resolved.relative_to(settings.project_root))
    except ValueError:
        stored[kind] = str(resolved)  # outside the project — keep it absolute
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(stored, indent=2) + "\n")
    return resolved


def documents() -> list[Path]:
    """Source documents in the whitepaper folder, newest first."""
    folder = get("whitepapers")
    if not folder.is_dir():
        return []
    found = [p for p in folder.iterdir()
             if p.suffix.lower() in DOCUMENT_SUFFIXES
             and (p.is_file() or p.suffix.lower() == ".rtfd")]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def external_files() -> list[Path]:
    folder = get("external")
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTERNAL_SUFFIXES)


def resolve_document(given: str | None) -> Path | None:
    """Turn whatever the user passed into a document path.

    Accepts a full path, a bare filename inside the whitepaper folder, or
    nothing at all when the folder holds exactly one document. Returns
    None when it cannot be resolved without guessing — picking one of
    several is the caller's job to ask about, not this function's to
    decide.
    """
    if given:
        given = given.strip()
        direct = Path(given).expanduser()
        if not direct.is_absolute():
            direct = settings.project_root / direct
        if direct.exists():
            return direct
        inside = get("whitepapers") / given
        return inside if inside.exists() else None
    docs = documents()
    return docs[0] if len(docs) == 1 else None


def describe() -> dict[str, Any]:
    return {
        "whitepapers": {"path": get("whitepapers"), "files": documents()},
        "external": {"path": get("external"), "files": external_files()},
    }
