"""How a failed publish is classified, recorded and surfaced.

2026-09-21, /grill-me design session. Before this, `step5_publish()`
answered every platform error the same way: print a warning and degrade
to a "simulated publish", which was then recorded as published and queued
for measurement. So a post that Bluesky rejected for being 22 characters
over the limit looked exactly like a post on a platform whose adapter has
not been written yet — and the browser said `Published.` either way. The
operator found out days later, by noticing the post was not on the
account.

Three kinds of failure, which want three different responses:

  CONTENT     the post itself is the problem (rejected for its length or
              its content). The same bytes will fail forever, so retrying
              unchanged is pointless — it needs a revision and another
              human decision.
  ENVIRONMENT nothing is wrong with the post (rate limit, network,
              expired credentials). The identical bytes will succeed
              later, so revising it would only damage a good post.
  TECHNICAL   an unexpected failure — a bug here, or a platform doing
              something undocumented. Nothing is assumed about it, and it
              is flagged loudly rather than smoothed over.

Every one of them is written to the audit log with its full reason
string, which is also what the repeat-error counter reads (see
error_index.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from .platforms.base import (
    PlatformAPIError,
    PlatformAuthError,
    PlatformConfigError,
    PlatformContentRejected,
    PlatformRateLimitError,
)
from .publish_guard import DraftNotPublishable

CONTENT = "content"
ENVIRONMENT = "environment"
TECHNICAL = "technical"


@dataclass(frozen=True)
class PublishFailure:
    kind: str
    error_type: str
    reason: str

    @property
    def retry_unchanged(self) -> bool:
        """True when the same bytes are worth sending again later."""
        return self.kind == ENVIRONMENT

    @property
    def needs_revision(self) -> bool:
        return self.kind == CONTENT

    def explain(self) -> str:
        if self.kind == CONTENT:
            return (f"The post itself was rejected ({self.reason}). Sending the same text again "
                    "will fail the same way — it needs a revision and your approval.")
        if self.kind == ENVIRONMENT:
            return (f"The platform could not be reached or refused the request ({self.reason}). "
                    "Nothing is wrong with the post; it is unchanged and can be sent again once "
                    "the condition clears.")
        return (f"Unexpected failure ({self.error_type}: {self.reason}). This is not a known "
                "failure mode — treat it as a bug until shown otherwise.")


def classify(error: Exception) -> PublishFailure:
    """Which of the three kinds an exception is.

    The split rides on the exception hierarchy that already exists in
    platforms/base.py, rather than on matching message text: auth and rate
    limit are their own classes precisely because they mean something
    different from a generic API error.
    """
    error_type, reason = type(error).__name__, str(error)
    if isinstance(error, (DraftNotPublishable, PlatformContentRejected)):
        return PublishFailure(CONTENT, error_type, reason)
    if isinstance(error, (PlatformRateLimitError, PlatformAuthError, PlatformConfigError)):
        return PublishFailure(ENVIRONMENT, error_type, reason)
    if isinstance(error, PlatformAPIError):
        # A plain PlatformAPIError covers both "the platform said no to
        # this post" and "the network died". The adapter knows which; we
        # do not, so this is deliberately the TECHNICAL bucket rather than
        # a guess dressed up as a classification.
        return PublishFailure(TECHNICAL, error_type, reason)
    return PublishFailure(TECHNICAL, error_type, reason)
