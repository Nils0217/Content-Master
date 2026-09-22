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
from dataclasses import dataclass, field
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


class PlatformContentRejected(PlatformAPIError):
    """The platform looked at this specific post and said no.

    2026-09-21: separated from the generic PlatformAPIError so a failure
    can be classified honestly (see publish_failure.py). Only the adapter
    can tell "your text is unacceptable" from "the network died", and the
    two need opposite responses — the first needs the post revised, the
    second needs the identical post sent again later. Raise this only when
    the platform's answer is about the content itself.
    """


class PostNotFoundError(PlatformAPIError):
    """The post no longer exists on the platform — almost always because
    a human deleted it by hand.

    2026-09-19: split out of the generic PlatformAPIError because callers
    must NOT treat it as "the call failed, degrade gracefully". It is a
    successful call with a definite answer, and the old behavior was
    actively destructive: pipeline._pull_checkpoint_metrics() caught the
    generic error and fell back to metrics_store.mock_metrics(), which
    invents plausible-looking random numbers — so a post deleted from the
    Bluesky web UI got fabricated engagement written into the ledger as
    if it were a live reading. It is also why the audit log said 14 posts
    while only 12 existed online (see docs/ERA0_SNAPSHOT.md).

    A rate limit or network blip is a PlatformAPIError and should be
    retried later; this one should be recorded and never retried.
    """


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


@dataclass(frozen=True)
class PlatformConstraints:
    """What a platform will and will not accept, declared by its adapter.

    2026-09-21. These are the platform's OWN published rules — hard,
    absolute, knowable before anything is generated. They exist because a
    322-character draft was generated, human-approved, and only found to
    be over Bluesky's 300-character limit when Bluesky rejected it. The
    limit had been sitting in `BlueskyPlatform.max_post_chars` the whole
    time; the generation prompt asked for "under 280 characters" as a
    hardcoded string in four separate places, and nothing connected the
    two. Now there is exactly one source, and generation, review and
    publishing all read it.

    Deliberately NOT the place for things we have *observed* about a
    platform ("posts with links seem to get less reach"). Those are
    findings with evidence, confidence and an expiry date, they inform
    rather than forbid, and they belong with the product's own learned
    rules — see docs/SCHEDULE.md. Giving an unverified guess the same
    authority as a published specification is how a guess becomes
    permanent.
    """
    max_post_chars: int | None = None
    max_images: int | None = None
    max_image_alt_chars: int | None = None
    supported_image_types: tuple[str, ...] = ()

    def describe_for_prompt(self) -> str:
        """The hard rules, phrased for the generation prompt. Only the
        constraints this platform actually declares — an adapter that
        knows nothing about image limits should not be asserting any.
        """
        parts = []
        if self.max_post_chars:
            parts.append(f"at most {self.max_post_chars} characters (this is a hard platform "
                         "limit — a post over it is rejected outright, not truncated)")
        if self.max_images:
            parts.append(f"at most {self.max_images} image(s)")
        if self.max_image_alt_chars:
            parts.append(f"image alt text at most {self.max_image_alt_chars} characters")
        return "; ".join(parts)

    def violations(self, text: str) -> list[str]:
        """Every hard rule `text` breaks. Empty list means publishable as
        far as the platform's own rules are concerned.
        """
        problems = []
        if self.max_post_chars and len(text) > self.max_post_chars:
            problems.append(
                f"{len(text)} characters, {len(text) - self.max_post_chars} over the "
                f"platform's hard limit of {self.max_post_chars}"
            )
        return problems


class Platform(ABC):
    """One social/content platform. Instantiate via
    `platforms.registry.get_platform(name)`, not directly.
    """

    name: str

    # Every hard rule this platform publishes, in one object. Generation,
    # review and publishing all read it, so the numbers cannot drift apart
    # the way `max_post_chars = 300` and a hardcoded "under 280 characters"
    # in the generation prompt did (see PlatformConstraints).
    constraints: PlatformConstraints = PlatformConstraints()

    @property
    def max_post_chars(self) -> int | None:
        """Kept as a property so existing callers keep working, but
        `constraints` is the declaration — an adapter should set that, not
        this.
        """
        return self.constraints.max_post_chars

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

        Raises PostNotFoundError if the post is gone (deleted by hand) —
        that is a definite answer, not a failure, and callers must not
        substitute made-up numbers for it.
        """

    def get_post_metrics_batch(self, post_refs: list[str]) -> dict[str, dict[str, Any]]:
        """Same as get_post_metrics() for many posts at once, keyed by
        post_ref. Refs that no longer exist are simply absent from the
        returned dict — that absence is what the caller reads as "deleted"
        (no exception, because one deleted post must not abort the rest of
        the batch).

        Default implementation loops. Override it when the platform has a
        real batch endpoint: `contentmaster refresh` re-polls every post
        under 30 days old on every run, so an adapter that loops turns one
        request into N (see bluesky.py, which folds the whole batch into a
        single app.bsky.feed.getPosts call).
        """
        out: dict[str, dict[str, Any]] = {}
        for ref in post_refs:
            try:
                out[ref] = self.get_post_metrics(ref)
            except PostNotFoundError:
                continue
        return out
