"""Deprecated location — Bluesky is now one Platform adapter among several.
Use `from .platforms.registry import get_platform; get_platform("bluesky")`,
or `from .platforms.bluesky import BlueskyPlatform` directly. This module
only re-exports for anything still importing the old path.
"""
from .platforms.base import (  # noqa: F401
    PlatformAPIError as BlueskyAPIError,
    PlatformAuthError as BlueskyAuthError,
    PlatformConfigError as BlueskyConfigError,
    PlatformRateLimitError as BlueskyRateLimitError,
    Post as BlueskyPost,
)
from .platforms.bluesky import BlueskyPlatform as BlueskyClient  # noqa: F401
