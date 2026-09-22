"""Bluesky (AT Protocol) — the first Platform adapter implementation.

Uses the official `atproto` SDK (https://atproto.blue). Authenticates with
an app password, not the account password — same handle/app-password model
GitHub Actions, third-party clients, etc. use.

SECURITY: BLUESKY_APP_PASSWORD is read from the environment and never
logged, printed, or included in any exception message this module raises.
`BlueskySettings.app_password` is repr=False for the same reason (see
../config.py) — do not add a __repr__/__str__ here that touches it either.
"""
from __future__ import annotations

from typing import Any

from atproto_client.exceptions import (
    AtProtocolError,
    NetworkError,
    RateLimitExceededError,
    UnauthorizedError,
)

from ..config import BlueskySettings, settings
from .base import (
    Platform,
    PlatformAPIError,
    PlatformAuthError,
    PlatformConfigError,
    PlatformRateLimitError,
    PlatformConstraints,
    PlatformContentRejected,
    Post,
    PostNotFoundError,
)


class BlueskyPlatform(Platform):
    name = "bluesky"
    # Bluesky's own published limits. 300 graphemes for the post; 4 images
    # per post; 2000 characters of alt text each.
    constraints = PlatformConstraints(
        max_post_chars=300,
        max_images=4,
        max_image_alt_chars=2000,
        supported_image_types=("image/jpeg", "image/png", "image/webp", "image/gif"),
    )

    def __init__(self, cfg: BlueskySettings | None = None):
        self.cfg = cfg or settings.bluesky
        self._client = None  # lazy: only import/construct atproto.Client on first use

    def _require_config(self) -> None:
        if not self.cfg.configured:
            missing = [
                var_name
                for var_name, val in (("BLUESKY_HANDLE", self.cfg.handle), ("BLUESKY_APP_PASSWORD", self.cfg.app_password))
                if not val
            ]
            raise PlatformConfigError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Set them in your shell or in .env (see env.example) — never hardcode them."
            )

    def _get_client(self):
        if self._client is not None:
            return self._client
        self._require_config()
        from atproto import Client  # imported lazily so config errors surface first

        client = Client()
        try:
            client.login(login=self.cfg.handle, password=self.cfg.app_password)
        except UnauthorizedError as e:
            raise PlatformAuthError(
                f"Bluesky login failed for handle '{self.cfg.handle}': invalid handle or app "
                "password. Generate a fresh app password at bsky.app -> Settings -> App "
                "Passwords (do not use your main account password)."
            ) from e
        except RateLimitExceededError as e:
            raise PlatformRateLimitError("Bluesky rate limit hit while logging in. Wait and retry.") from e
        except NetworkError as e:
            raise PlatformAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise PlatformAPIError(f"Bluesky API error during login: {e}") from e
        self._client = client
        return client

    def test_connection(self) -> dict[str, Any]:
        client = self._get_client()
        profile = client.me
        return {
            "handle": profile.handle,
            "did": profile.did,
            "display_name": getattr(profile, "display_name", None),
        }

    def fetch_public_posts(self, query: str, limit: int = 5) -> list[Post]:
        """Search Bluesky's public post index (app.bsky.feed.searchPosts) —
        no following/timeline access needed, just an authenticated session.
        """
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        client = self._get_client()
        try:
            response = client.app.bsky.feed.search_posts({"q": query, "limit": limit, "sort": "latest"})
        except RateLimitExceededError as e:
            raise PlatformRateLimitError("Bluesky rate limit hit while searching posts. Wait and retry.") from e
        except NetworkError as e:
            raise PlatformAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise PlatformAPIError(f"Bluesky API error during search: {e}") from e

        return [
            Post(
                id=item.uri,
                author=item.author.handle,
                text=item.record.text,
                created_at=str(item.record.created_at),
                like_count=item.like_count or 0,
            )
            for item in response.posts
        ]

    def publish_post(
        self, text: str, image: bytes | None = None, image_alt: str = "",
    ) -> dict[str, Any]:
        # 2026-09-19 (code scan): was a bare ValueError, which
        # pipeline.step5_publish() does not catch — a single over-long
        # draft crashed the entire run instead of degrading. A
        # PlatformAPIError travels the same path as any other publish
        # failure (printed warning + simulated publish), so the operator
        # sees it without losing the rest of the run.
        if len(text) > self.max_post_chars:
            raise PlatformContentRejected(
                f"Bluesky posts are capped at {self.max_post_chars} characters, got {len(text)}. "
                "Shorten the draft in review and approve it again."
            )
        client = self._get_client()
        try:
            # send_image() (not post()) when there's an image to attach —
            # same atproto Client, just the one-image variant. 2026-09-16,
            # Phase 4: see image_generator.py.
            if image:
                result = client.send_image(text=text, image=image, image_alt=image_alt)
            else:
                result = client.post(text=text)
        except RateLimitExceededError as e:
            raise PlatformRateLimitError("Bluesky rate limit hit while posting. Wait and retry.") from e
        except NetworkError as e:
            raise PlatformAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise PlatformAPIError(f"Bluesky API error while posting: {e}") from e
        return {"uri": result.uri, "cid": result.cid}

    # app.bsky.feed.getPosts caps at 25 URIs per call.
    _GET_POSTS_MAX_URIS = 25

    def _reading(self, post_ref: str, post: Any) -> dict[str, Any]:
        return {
            "source": "bluesky",
            "uri": post_ref,
            "like_count": post.like_count or 0,
            "repost_count": post.repost_count or 0,
            "reply_count": post.reply_count or 0,
            # 2026-09-20: verified live against this account — getPosts
            # returns real integers for both, not None, so no extra
            # endpoint is needed. bookmark_count is a weight-3 signal
            # (see scoring.py); quote_count is captured but deliberately
            # never scored, because a quote can be a dunk.
            "bookmark_count": post.bookmark_count or 0,
            "quote_count": post.quote_count or 0,
        }

    def _get_posts(self, uris: list[str]):
        client = self._get_client()
        try:
            return client.get_posts(uris).posts
        except RateLimitExceededError as e:
            raise PlatformRateLimitError("Bluesky rate limit hit while fetching post metrics. Wait and retry.") from e
        except NetworkError as e:
            raise PlatformAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise PlatformAPIError(f"Bluesky API error while fetching post metrics: {e}") from e

    def get_post_metrics(self, post_ref: str) -> dict[str, Any]:
        """`post_ref` is the `uri` returned by publish_post(). Freshly-posted
        content will usually read 0s — that's a real number, not a bug.
        """
        posts = self._get_posts([post_ref])
        if not posts:
            # getPosts returns 200 with an empty list for a URI that is
            # gone — the same shape it would give a post still indexing,
            # which is why this used to be reported as "may still be
            # indexing". In practice nothing here is ever polled sooner
            # than 24h after publish (tracking_review.CHECKPOINT_MIN_AGE),
            # so at that age an empty result means deleted, not pending.
            raise PostNotFoundError(
                f"Bluesky returned no post for {post_ref} — it was almost certainly deleted."
            )
        return self._reading(post_ref, posts[0])

    def get_post_metrics_batch(self, post_refs: list[str]) -> dict[str, dict[str, Any]]:
        """One getPosts call per 25 URIs instead of one per post. Deleted
        posts are simply missing from the response, which is exactly the
        "absent means gone" contract the base class documents.
        """
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(post_refs), self._GET_POSTS_MAX_URIS):
            chunk = post_refs[start:start + self._GET_POSTS_MAX_URIS]
            for post in self._get_posts(chunk):
                out[post.uri] = self._reading(post.uri, post)
        return out
