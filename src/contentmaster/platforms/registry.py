"""Platform registry — the single place that knows which platform names
exist and what implements them. `pipeline.py` and the CLI look platforms
up here by name; nothing else should import a specific platform class.

To add a real platform: write `platforms/<name>.py` implementing `Platform`
(see `base.py`), import its class below, and move its name from
PLANNED_PLATFORMS into PLATFORMS.
"""
from __future__ import annotations

from .base import Platform, PlatformConfigError
from .bluesky import BlueskyPlatform

PLATFORMS: dict[str, type[Platform]] = {
    "bluesky": BlueskyPlatform,
}

# Named, deliberately not implemented yet — placeholders so `contentmaster
# platforms` can show the intended lineup (see docs/SCHEDULE.md) without a
# stub class per platform. Move a name up to PLATFORMS once it's built.
PLANNED_PLATFORMS: list[str] = [
    "mastodon",
    "x",
    "instagram",
    "facebook",
    "youtube",
]


def get_platform(name: str) -> Platform:
    """Instantiate the platform adapter registered under `name`."""
    cls = PLATFORMS.get(name)
    if cls is not None:
        return cls()
    if name in PLANNED_PLATFORMS:
        raise PlatformConfigError(
            f"'{name}' is a planned platform, not implemented yet. "
            f"Available now: {', '.join(PLATFORMS) or '(none)'}."
        )
    raise PlatformConfigError(
        f"Unknown platform '{name}'. Available: {', '.join(PLATFORMS) or '(none)'}. "
        f"Planned: {', '.join(PLANNED_PLATFORMS)}."
    )
