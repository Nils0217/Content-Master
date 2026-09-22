"""Images the user supplied for a product, preferred over generated ones.

2026-09-22. Two problems this answers at once.

The first is that an image model cannot draw a product it has never seen.
Given the name "Fluffy roommate" FLUX invented a glowing fur-ball, and
another time a pillow. A confirmed category (topic_index.load_category)
helps — "a domestic cat" is drawable where a brand name is not — but a
category is a class, not this particular product.

The second is that some products cannot be photographed at all. Software,
a service, a course: the honest illustration is a screenshot or the thing
being used, and no amount of prompt engineering will make a generator
produce your actual dashboard.

So: if the user has put images in this product's folder, those are used.
Generation is what happens when there is nothing to use — not the default
for products that have real assets. A real photograph of the real product
beats a generated approximation for any product type, which is why this
is not limited to the software case that prompted it.

Supplied images still go through human_loop.review_image(). The file
being real does not mean it is the right file, or current, or the one
meant for this post.

Layout — one folder per product, named by the same slug the topic index
uses, so the two stay findable together:

    product_assets/
        fluffy-roommate/
            playpen-front.jpg
            playpen-in-use.png
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .slug import slugify

ASSETS_DIR = settings.data_root / "product_assets"
USAGE_PATH = settings.data_root / "plays" / "_asset_usage.jsonl"

# What Bluesky accepts and PIL-free reading can pass straight through.
SUPPORTED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def product_dir(product_name: str) -> Path:
    return ASSETS_DIR / slugify(product_name, default="product")


def available(product_name: str) -> list[Path]:
    """Every usable image the user put in this product's folder, sorted by
    name so the rotation below is reproducible.
    """
    folder = product_dir(product_name)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)


def _usage_counts() -> dict[str, int]:
    if not USAGE_PATH.exists():
        return {}
    counts: dict[str, int] = {}
    for line in USAGE_PATH.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        path = record.get("path")
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def _record_use(path: Path, product_name: str) -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_PATH.open("a") as fh:
        fh.write(json.dumps({"path": str(path), "product": product_name,
                             "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")


def next_image(product_name: str) -> tuple[bytes, Path] | None:
    """The least-used supplied image, or None if the folder is empty.

    Least-used-first rather than random, for the same reason topic
    selection works that way: it is reproducible, and it stops one image
    appearing on post after post while others are never used at all.
    """
    candidates = available(product_name)
    if not candidates:
        return None
    counts = _usage_counts()
    chosen = min(candidates, key=lambda p: (counts.get(str(p), 0), p.name))
    try:
        data = chosen.read_bytes()
    except OSError as e:
        print(f"[warn] Could not read {chosen} ({e}); falling back to a generated image.")
        return None
    _record_use(chosen, product_name)
    return data, chosen


def describe(product_name: str) -> dict[str, Any]:
    """For the startup banner and `contentmaster status`."""
    files = available(product_name)
    return {"folder": str(product_dir(product_name)), "count": len(files),
            "names": [p.name for p in files]}
