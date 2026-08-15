"""The tracker abstraction: one interface every GPS platform implements."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from src.ratelimit import RateLimiter, RateLimitPolicy, load_policy

# File extension written for each download format. Shared by every tracker --
# a .fit file is a .fit file wherever it came from.
FORMAT_EXTENSIONS = {"FIT": "fit", "GPX": "gpx", "TCX": "tcx"}


class TrackerAuthError(Exception):
    """Raised when a tracker cannot authenticate and has no fallback."""


class ActivityDownloadError(Exception):
    """Raised when one activity cannot be downloaded.

    Recoverable: the download loop logs it and moves on to the next activity,
    unlike `TrackerAuthError`, which ends the run for that tracker.
    """


class UnsafeActivityIdError(ValueError):
    """Raised when a tracker returns an id that is not safe to use in a path."""


@dataclass(frozen=True)
class Activity:
    """One activity, normalized across trackers.

    `payload` carries opaque per-tracker data from listing through to download
    (Wahoo, for example, only learns a workout's file URL when it lists it). It
    is excluded from comparison so two `Activity`s are equal when they identify
    the same activity.
    """

    id: str
    name: str
    payload: Mapping[str, Any] = field(default_factory=dict, compare=False)


class Tracker(ABC):
    """A GPS platform that activities can be listed and downloaded from.

    To add a tracker: implement this interface in `src/trackers/<name>.py`, then
    add the class to `TRACKER_CLASSES` in `src/trackers/__init__.py`. Nothing
    else in the pipeline needs to change.

    Implementations must not perform I/O at import time -- CI smoke-tests the
    image with `python -c "import src.main"`.
    """

    #: Registry key, env var prefix, and output filename prefix. Keep it a
    #: lowercase ASCII alphanumeric word, since it becomes part of a filename.
    name: ClassVar[str]

    #: Which of `FORMAT_EXTENSIONS` this tracker can supply. Requested formats
    #: outside this set are skipped with a warning rather than failing the run.
    supported_formats: ClassVar[frozenset[str]]

    #: How hard a run pushes this API. Every tracker must replace this with the
    #: published limits of its own platform, or with a conservative guess when
    #: the platform publishes none. State the source in a comment beside it, so
    #: a later reader knows whether the numbers are a fact or a judgement.
    #: `load_policy` then lets an operator override any field with a
    #: `<TRACKER>_` environment variable, so no tracker parses these itself.
    #: The default here is deliberately slow: a tracker that forgets to set it
    #: is protected, not fast.
    rate_limit: ClassVar[RateLimitPolicy] = RateLimitPolicy()

    #: Built on first use, because a tracker sets its own `rate_limit` (Wahoo
    #: picks between two published tiers) before anything reads the limiter.
    _limiter: "RateLimiter | None" = None

    @property
    def limiter(self) -> RateLimiter:
        """The rate limiter of this instance.

        Every counted request of a tracker goes through `limiter.call`. A
        request that the platform exempts from its limits goes through
        `limiter.retry`, which retries but spends no budget.
        """
        if self._limiter is None:
            self._limiter = RateLimiter(load_policy(self.name, self.rate_limit), name=self.name)
        return self._limiter

    @classmethod
    @abstractmethod
    def from_env(cls, tokens_dir: str) -> "Tracker":
        """Build an unauthenticated instance from env vars and Docker secrets.

        Args:
            tokens_dir: Root token directory. Trackers store under
                `<tokens_dir>/<name>` so they never collide.
        """

    @abstractmethod
    def authenticate(self) -> None:
        """Prepare the tracker for use.

        Raises:
            TrackerAuthError: If the tracker cannot authenticate.
        """

    @abstractmethod
    def list_activities(self, start_date: str, end_date: str) -> list[Activity]:
        """List activities in an inclusive `YYYY-MM-DD` date range."""

    @abstractmethod
    def download(self, activity: Activity, fmt: str) -> bytes:
        """Return the final file bytes for one activity in one format.

        Implementations own any unwrapping (Garmin, for example, serves FIT
        inside a zip), so the caller writes what it gets straight to disk.

        Raises:
            ActivityDownloadError: If this one activity cannot be downloaded.
        """

    @classmethod
    @abstractmethod
    def interactive_setup(cls, tokens_dir: str) -> None:
        """Run the one-time interactive login that seeds the token store."""
