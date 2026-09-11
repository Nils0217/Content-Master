"""Bluesky (AT Protocol) integration — a public-post data source feeding the
same Cognee ingestion pipeline that already handles the whitepaper PDF
(see cognee_client.add_raw_texts and pipeline.py's step1_cognee_extract).

Uses the official `atproto` SDK (https://atproto.blue). Authenticates with
an app password, not the account password — same handle/app-password model
GitHub Actions, third-party clients, etc. use.

SECURITY: BLUESKY_APP_PASSWORD is read from the environment and never
logged, printed, or included in any exception message this module raises.
`BlueskySettings.app_password` is repr=False for the same reason (see
config.py) — do not add a __repr__/__str__ here that touches it either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atproto_client.exceptions import (
    AtProtocolError,
    NetworkError,
    RateLimitExceededError,
    UnauthorizedError,
)

from .config import BlueskySettings, settings


class BlueskyConfigError(RuntimeError):
    """Raised when BLUESKY_HANDLE / BLUESKY_APP_PASSWORD are missing."""


class BlueskyAuthError(RuntimeError):
    """Raised when login fails (bad handle/app password, locked account, etc.)."""


class BlueskyRateLimitError(RuntimeError):
    """Raised when Bluesky's API rate limit is hit."""


class BlueskyAPIError(RuntimeError):
    """Raised for other API/network failures talking to Bluesky."""


@dataclass
class BlueskyPost:
    """Normalized shape — matches the plain-text `raw_data` entries Cognee's
    /api/v1/add already accepts (see CogneeClient.add_raw_texts), so a post
    list here can be fed straight into cognee.add_raw_texts([p.as_text() ...]).
    """

    uri: str
    author_handle: str
    text: str
    created_at: str
    like_count: int

    def as_text(self) -> str:
        """Flatten to one plain-text blob for Cognee ingestion."""
        return f"[Bluesky @{self.author_handle}, {self.created_at}] {self.text}"


class BlueskyClient:
    def __init__(self, cfg: BlueskySettings | None = None):
        self.cfg = cfg or settings.bluesky
        self._client = None  # lazy: only import/construct atproto.Client on first use

    def _require_config(self) -> None:
        if not self.cfg.configured:
            missing = [
                name
                for name, val in (("BLUESKY_HANDLE", self.cfg.handle), ("BLUESKY_APP_PASSWORD", self.cfg.app_password))
                if not val
            ]
            raise BlueskyConfigError(
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
            raise BlueskyAuthError(
                f"Bluesky login failed for handle '{self.cfg.handle}': invalid handle or app "
                "password. Generate a fresh app password at bsky.app -> Settings -> App "
                "Passwords (do not use your main account password)."
            ) from e
        except RateLimitExceededError as e:
            raise BlueskyRateLimitError("Bluesky rate limit hit while logging in. Wait and retry.") from e
        except NetworkError as e:
            raise BlueskyAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise BlueskyAPIError(f"Bluesky API error during login: {e}") from e
        self._client = client
        return client

    def test_connection(self) -> dict[str, Any]:
        """Authenticate and return non-sensitive profile info. Raises
        BlueskyConfigError / BlueskyAuthError / BlueskyRateLimitError /
        BlueskyAPIError on failure — never returns a partial/ambiguous result.
        """
        client = self._get_client()
        profile = client.me
        return {
            "handle": profile.handle,
            "did": profile.did,
            "display_name": getattr(profile, "display_name", None),
        }

    def fetch_public_posts(self, query: str, limit: int = 5) -> list[BlueskyPost]:
        """Search Bluesky's public post index (app.bsky.feed.searchPosts) —
        no following/timeline access needed, just an authenticated session.
        """
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        client = self._get_client()
        try:
            response = client.app.bsky.feed.search_posts({"q": query, "limit": limit, "sort": "latest"})
        except RateLimitExceededError as e:
            raise BlueskyRateLimitError("Bluesky rate limit hit while searching posts. Wait and retry.") from e
        except NetworkError as e:
            raise BlueskyAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise BlueskyAPIError(f"Bluesky API error during search: {e}") from e

        posts = []
        for item in response.posts:
            posts.append(
                BlueskyPost(
                    uri=item.uri,
                    author_handle=item.author.handle,
                    text=item.record.text,
                    created_at=str(item.record.created_at),
                    like_count=item.like_count or 0,
                )
            )
        return posts

    # -- publish + pull-back (real posting layer) --------------------------
    def publish_post(self, text: str) -> dict[str, Any]:
        """Post to this account's own timeline for real. Called only after a
        human has approved the draft (see human_loop.review_draft) — never
        call this on unreviewed text.
        """
        if len(text) > 300:
            raise ValueError(f"Bluesky posts are capped at 300 characters, got {len(text)}")
        client = self._get_client()
        try:
            result = client.post(text=text)
        except RateLimitExceededError as e:
            raise BlueskyRateLimitError("Bluesky rate limit hit while posting. Wait and retry.") from e
        except NetworkError as e:
            raise BlueskyAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise BlueskyAPIError(f"Bluesky API error while posting: {e}") from e
        return {"uri": result.uri, "cid": result.cid}

    def get_post_metrics(self, uri: str) -> dict[str, Any]:
        """Pull real engagement back for a post this account made (or any
        public post uri). Freshly-posted content will usually read 0s —
        that's a real number, not a bug.
        """
        client = self._get_client()
        try:
            response = client.get_posts([uri])
        except RateLimitExceededError as e:
            raise BlueskyRateLimitError("Bluesky rate limit hit while fetching post metrics. Wait and retry.") from e
        except NetworkError as e:
            raise BlueskyAPIError(f"Network error reaching Bluesky: {e}") from e
        except AtProtocolError as e:
            raise BlueskyAPIError(f"Bluesky API error while fetching post metrics: {e}") from e
        if not response.posts:
            raise BlueskyAPIError(f"Post not found (may still be indexing): {uri}")
        post = response.posts[0]
        return {
            "uri": uri,
            "like_count": post.like_count or 0,
            "repost_count": post.repost_count or 0,
            "reply_count": post.reply_count or 0,
        }
