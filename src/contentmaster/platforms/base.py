"""The Platform adapter interface.

Bluesky is one destination among many the loop can eventually publish to
and pull results from (Instagram, Facebook, X, YouTube, Mastodon, ...).
Nothing outside this package should import a specific platform's client
directly — go through `platforms.registry.get_platform(name)` instead, so
`pipeline.py` and the CLI work with any implemented platform the same way.

To add a new platform: subclass `Platform`, implement at minimum
`test_connection`, `publish_post`, `get_post_metrics` (and
`fetch_public_posts` if the platform supports search), then register it in
`registry.py`. See `bluesky.py` for a complete reference implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class PlatformConfigError(RuntimeError):
    """Required credentials/config for a platform are missing."""


class PlatformAuthError(RuntimeError):
    """Login/authentication with a platform failed."""


class PlatformRateLimitError(RuntimeError):
    """A platform's API rate limit was hit."""


class PlatformAPIError(RuntimeError):
    """Any other platform API/network failure."""


@dataclass
class Post:
    """Normalized shape a platform's fetch_public_posts() returns, so
    downstream code (e.g. Cognee ingestion) doesn't need to know which
    platform a post came from.
    """

    id: str
    author: str
    text: str
    created_at: str
    like_count: int = 0

    def as_text(self) -> str:
        """Flatten to one plain-text blob, e.g. for Cognee's add_raw_texts()."""
        return f"[@{self.author}, {self.created_at}] {self.text}"


class Platform(ABC):
    """One social/content platform. Instantiate via
    `platforms.registry.get_platform(name)`, not directly.
    """

    name: str

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Authenticate and return non-sensitive identity info (handle,
        account id, display name, ...). Raise PlatformConfigError /
        PlatformAuthError / PlatformRateLimitError / PlatformAPIError on
        failure — never return a partial/ambiguous result.
        """

    def fetch_public_posts(self, query: str, limit: int = 5) -> list[Post]:
        """Search this platform's public posts. Optional capability — not
        every platform exposes public search (e.g. Instagram/Facebook
        mostly don't via public API). Default: unsupported.
        """
        raise NotImplementedError(f"{self.name} does not support fetch_public_posts()")

    @abstractmethod
    def publish_post(self, text: str) -> dict[str, Any]:
        """Post to this account's own timeline for real. Only ever call
        this after human approval (see human_loop.review_draft) — never on
        unreviewed text. Returns a dict with at least a platform-specific
        reference (e.g. {"uri": ...} or {"id": ...}) that get_post_metrics()
        can use to look the post back up.
        """

    @abstractmethod
    def get_post_metrics(self, post_ref: str) -> dict[str, Any]:
        """Pull real engagement back for a post this account made. Returns
        a normalized dict with at least like_count/repost_count/reply_count
        (0 for anything the platform doesn't track) plus a "source" string
        naming the platform.
        """
