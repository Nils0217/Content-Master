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
    """Required credentials/config for a platform are missing.

    Deliberately NOT a PlatformAPIError: nothing was attempted against the
    platform at all, so callers that fall back on "the call failed" should
    treat this separately (it means "you haven't set this up yet").
    """


class PlatformAPIError(RuntimeError):
    """Any platform API/network failure.

    2026-09-19 (code scan): this is now the base class for the auth and
    rate-limit errors below, so a caller that only wants "the platform
    call failed, degrade gracefully" can catch this one and be sure it
    covers every runtime failure mode. Before this, all four classes
    inherited RuntimeError independently, so `except PlatformAPIError`
    silently missed auth/rate-limit failures — pipeline.step5_publish()
    and _pull_checkpoint_metrics() both did exactly that, and would crash
    the whole run on a rate limit instead of falling back. Callers that
    DO want to tell them apart still can: list the subclasses first, the
    way cli.py's `connect` command already does.
    """


class PlatformAuthError(PlatformAPIError):
    """Login/authentication with a platform failed."""


class PlatformRateLimitError(PlatformAPIError):
    """A platform's API rate limit was hit."""


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

    # 2026-09-19 (code scan): every platform has a different post length
    # cap, and until now only bluesky.py knew its own — so nothing
    # upstream (draft generation, the review UI) could warn before a
    # too-long post reached publish_post() and blew up. Declared here so
    # any caller can check `get_platform(ch).max_post_chars` generically.
    # None means "no known cap".
    max_post_chars: int | None = None

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
    def publish_post(
        self, text: str, image: bytes | None = None, image_alt: str = "",
    ) -> dict[str, Any]:
        """Post to this account's own timeline for real. Only ever call
        this after human approval (see human_loop.review_draft) — never on
        unreviewed text. Returns a dict with at least a platform-specific
        reference (e.g. {"uri": ...} or {"id": ...}) that get_post_metrics()
        can use to look the post back up.

        `image`/`image_alt` (2026-09-16, Phase 4 — see image_generator.py):
        optional. A platform with no image support yet can just ignore
        them (post text-only) rather than raise — image generation itself
        already degrades gracefully when unconfigured, so a platform
        adapter doing the same keeps that chain unbroken end to end.
        `image_alt` should always be given when `image` is (accessibility;
        Bluesky's own client strongly encourages it).
        """

    @abstractmethod
    def get_post_metrics(self, post_ref: str) -> dict[str, Any]:
        """Pull real engagement back for a post this account made. Returns
        a normalized dict with at least like_count/repost_count/reply_count
        (0 for anything the platform doesn't track) plus a "source" string
        naming the platform.
        """
