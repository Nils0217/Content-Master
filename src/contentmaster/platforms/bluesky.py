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
    Post,
)


class BlueskyPlatform(Platform):
    name = "bluesky"

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

    def publish_post(self, text: str) -> dict[str, Any]:
        if len(text) > 300:
            raise ValueError(f"Bluesky posts are capped at 300 characters, got {len(text)}")
        client = self._get_client()
        try:
            result = client.post(text=text)
        except RateLimitExceededError as e:
            raise PlatformRateLimitError("Bluesky rate limit hit while posting. Wait and retry.") from e
        except NetworkError as e:
            raise PlatformAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise PlatformAPIError(f"Bluesky API error while posting: {e}") from e
        return {"uri": result.uri, "cid": result.cid}

    def get_post_metrics(self, post_ref: str) -> dict[str, Any]:
        """`post_ref` is the `uri` returned by publish_post(). Freshly-posted
        content will usually read 0s — that's a real number, not a bug.
        """
        client = self._get_client()
        try:
            response = client.get_posts([post_ref])
        except RateLimitExceededError as e:
            raise PlatformRateLimitError("Bluesky rate limit hit while fetching post metrics. Wait and retry.") from e
        except NetworkError as e:
            raise PlatformAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise PlatformAPIError(f"Bluesky API error while fetching post metrics: {e}") from e
        if not response.posts:
            raise PlatformAPIError(f"Post not found (may still be indexing): {post_ref}")
        post = response.posts[0]
        return {
            "source": "bluesky",
            "uri": post_ref,
            "like_count": post.like_count or 0,
            "repost_count": post.repost_count or 0,
            "reply_count": post.reply_count or 0,
        }
