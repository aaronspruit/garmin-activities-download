"""Rate limits, retries, and the request budget of one run.

Every tracker API has different limits, and only some platforms publish them.
A tracker therefore declares its own `RateLimitPolicy` as a class attribute, and
`load_policy` lets an operator replace any part of that policy with environment
variables. A new tracker gets the pacing, the retries, and the per-run budget as
soon as it sets `rate_limit`. It needs no other change.

The limiter paces the requests that count against a limit. Each tracker decides
which of its requests those are. Wahoo, for example, does not count the file
downloads, so only the workout list pages consume its budget.

A run never waits out a long limit. If the next request needs more than
`max_wait` seconds, the limiter raises `BudgetExhaustedError` and the run stops early.
The dedup markers make this safe. The next run continues from the same point, so
a large backlog drains across several runs.

This module reads the environment directly, for the same reason as `src/env.py`:
`config` imports `trackers`, and a tracker needs its policy, so a policy loader
inside `config` would make an import cycle.
"""

import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)

# Grammar of `<TRACKER>_RATE_LIMIT`: `20/60, 300/3600` is 20 requests in 60
# seconds and 300 requests in 3600 seconds.
_WINDOW_SEPARATOR = ","
_WINDOW_DIVIDER = "/"

# Value that removes every window of a policy, for an operator who paces the
# runs with the schedule instead.
_NO_LIMIT = "none"


class RateLimitError(Exception):
    """The tracker refused a request because of its rate limit.

    Retryable. `retry_after` holds the number of seconds that the tracker asked
    for, when it sent one.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(Exception):
    """A temporary failure of the tracker API, such as a 5xx status. Retryable."""


class BudgetExhaustedError(Exception):
    """The run reached a rate limit and must stop early.

    Not a failure. The caller records what it downloaded and exits normally,
    because the next scheduled run continues from the same point.
    """


@dataclass(frozen=True)
class Window:
    """One limit: `limit` requests in `seconds` seconds."""

    limit: int
    seconds: float

    def __str__(self) -> str:
        return f"{self.limit}/{self.seconds:g}s"


@dataclass(frozen=True)
class RateLimitPolicy:
    """How hard one run pushes one tracker.

    Attributes:
        windows: The limits of the API, from the shortest window to the longest.
        min_interval: Smallest gap in seconds between two counted requests.
        max_retries: Number of retries after the first attempt fails.
        backoff_initial: Delay in seconds before the first retry. It doubles for
            each retry that follows.
        backoff_max: Largest delay in seconds that the backoff produces.
        max_wait: Longest single wait in seconds that a run accepts. A longer
            wait raises `BudgetExhaustedError` and stops the run.
        max_downloads: Largest number of files that one run writes. `0` removes
            this cap and lets the windows alone limit the run.
    """

    windows: tuple[Window, ...] = (Window(30, 60), Window(500, 3600), Window(3000, 86400))
    min_interval: float = 1.0
    max_retries: int = 3
    backoff_initial: float = 5.0
    backoff_max: float = 300.0
    max_wait: float = 300.0
    max_downloads: int = 0

    def describe(self) -> str:
        """One line that names every limit, for the startup log."""
        windows = ", ".join(str(window) for window in self.windows) or _NO_LIMIT
        return (
            f"windows={windows}, min_interval={self.min_interval:g}s, retries={self.max_retries}, "
            f"max_wait={self.max_wait:g}s, max_downloads={self.max_downloads or 'unlimited'}"
        )


class RateLimiter:
    """Paces and retries the requests of one tracker.

    The limiter counts the requests of each window in a sliding window, so a
    burst at the start of a run cannot exceed a published limit. It also keeps
    `min_interval` seconds between two requests.

    `sleep` and `clock` are replaceable, because the tests must not wait. Both
    default to `None` and resolve to the `time` module at each call, so a test
    that replaces `time.sleep` reaches a limiter that already exists.
    """

    def __init__(
        self,
        policy: RateLimitPolicy,
        name: str = "tracker",
        sleep=None,
        clock=None,
    ) -> None:
        self.policy = policy
        self.name = name
        self.requests = 0
        self._sleep_func = sleep
        self._clock_func = clock
        self._calls: list[deque[float]] = [deque() for _ in policy.windows]
        self._last: float | None = None
        self._blocked_until = 0.0

    def _sleep(self, seconds: float) -> None:
        (self._sleep_func or time.sleep)(seconds)

    def _clock(self) -> float:
        return (self._clock_func or time.monotonic)()

    def _wait_needed(self, now: float) -> float:
        """Seconds until the next counted request is inside every limit."""
        wait = max(0.0, self._blocked_until - now)

        if self._last is not None:
            wait = max(wait, self._last + self.policy.min_interval - now)

        for window, calls in zip(self.policy.windows, self._calls, strict=True):
            while calls and calls[0] <= now - window.seconds:
                calls.popleft()
            if len(calls) >= window.limit:
                wait = max(wait, calls[0] + window.seconds - now)

        return wait

    def acquire(self) -> None:
        """Wait for a free slot, then count one request.

        Raises:
            BudgetExhaustedError: If the wait is longer than `max_wait`.
        """
        now = self._clock()
        wait = self._wait_needed(now)

        if wait > self.policy.max_wait:
            raise BudgetExhaustedError(
                f"{self.name} needs {wait:.0f}s before its next request, which is more than the "
                f"{self.policy.max_wait:.0f}s that one run accepts. It made {self.requests} requests"
            )

        if wait > 0:
            logger.info("[%s] Rate limit reached, waiting %.1fs", self.name, wait)
            self._sleep(wait)
            now = self._clock()

        self._last = now
        self.requests += 1
        for calls in self._calls:
            calls.append(now)

    def penalize(self, seconds: float) -> None:
        """Block every counted request for this many seconds.

        Used when the tracker states its own wait, in a `Retry-After` header or
        in a rate limit header.
        """
        if seconds > 0:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)

    def call(self, func, *args, **kwargs):
        """Run one counted request, with pacing and retries."""
        return self._attempt(func, args, kwargs, counted=True)

    def retry(self, func, *args, **kwargs):
        """Run one request that no limit counts, with retries only.

        Wahoo exempts the file downloads from its limits, but a download still
        fails temporarily, so it still needs the retries.
        """
        return self._attempt(func, args, kwargs, counted=False)

    def _attempt(self, func, args, kwargs, counted: bool):
        for attempt in range(self.policy.max_retries + 1):
            if counted:
                self.acquire()
            try:
                return func(*args, **kwargs)
            except (RateLimitError, TransientError) as error:
                if attempt >= self.policy.max_retries:
                    raise
                delay = self._backoff(attempt, error)
                if delay > self.policy.max_wait:
                    raise BudgetExhaustedError(
                        f"{self.name} asked for a {delay:.0f}s wait, which is more than the "
                        f"{self.policy.max_wait:.0f}s that one run accepts: {error}"
                    ) from error
                logger.warning(
                    "[%s] %s Retry %d of %d in %.0fs",
                    self.name,
                    error,
                    attempt + 1,
                    self.policy.max_retries,
                    delay,
                )
                self._sleep(delay)

        # The loop above always returns or raises. This line keeps the function
        # total for a reader and for a type checker.
        raise AssertionError("unreachable")  # pragma: no cover

    def _backoff(self, attempt: int, error: Exception) -> float:
        """Exponential backoff with jitter, never shorter than the tracker asked for.

        The jitter spreads the retries of two containers that start together, so
        they do not repeat the same collision.
        """
        base = min(self.policy.backoff_max, self.policy.backoff_initial * 2**attempt)
        delay = base * (0.5 + random.random() * 0.5)

        retry_after = getattr(error, "retry_after", None)
        if retry_after:
            delay = max(delay, float(retry_after))
        return delay


def _parse_windows(raw: str, variable: str) -> tuple[Window, ...]:
    """Parse a `20/60, 300/3600` value into windows.

    The value `none` gives an empty result, which removes every window.
    """
    if raw.strip().lower() == _NO_LIMIT:
        return ()

    windows: list[Window] = []
    for entry in (part.strip() for part in raw.split(_WINDOW_SEPARATOR)):
        if not entry:
            continue

        count, divider, seconds = entry.partition(_WINDOW_DIVIDER)
        if not divider:
            raise ValueError(
                f"Invalid {variable} entry {entry!r}. Each entry is `REQUESTS/SECONDS`, for example `20/60`"
            )

        try:
            window = Window(limit=int(count.strip()), seconds=float(seconds.strip()))
        except ValueError:
            raise ValueError(
                f"Invalid {variable} entry {entry!r}. The number of requests and the number of seconds must be numbers"
            ) from None

        if window.limit < 1 or window.seconds <= 0:
            raise ValueError(
                f"Invalid {variable} entry {entry!r}. "
                f"The number of requests must be 1 or more, and the number of seconds must be more than 0"
            )
        windows.append(window)

    if not windows:
        raise ValueError(f"{variable} must not be empty. Set it to {_NO_LIMIT!r} to remove every limit")

    # Shortest window first, so a log of the policy reads in a useful order.
    return tuple(sorted(windows, key=lambda window: window.seconds))


def _read_number(names: list[str], convert, minimum: float):
    """Read the first variable of `names` that is set, and convert it.

    The names are given from the most specific to the least specific, so a
    `<TRACKER>_` variable replaces the shared one.
    """
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue

        try:
            value = convert(raw)
        except ValueError:
            raise ValueError(f"Invalid {name} value {raw!r}. It must be a number") from None

        if value < minimum:
            raise ValueError(f"Invalid {name} value {raw!r}. It must be {minimum:g} or more")
        return value
    return None


def load_policy(tracker: str, default: RateLimitPolicy) -> RateLimitPolicy:
    """Build the policy of one tracker from its defaults and the environment.

    The defaults hold the published limits of the tracker, or a conservative
    guess when the platform publishes none. Every field has an override, so an
    operator who watches their own account can raise or lower any of them.

    Two groups of variables exist. `<TRACKER>_RATE_LIMIT` and
    `<TRACKER>_MIN_INTERVAL` describe the API itself, so they have no shared
    form. The other variables describe how patient this deployment is, so each
    has a shared form that `<TRACKER>_` replaces.

    Args:
        tracker: Registry name of the tracker, such as `garmin`.
        default: The policy that the tracker class declares.

    Returns:
        The policy after every override that is set.

    Raises:
        ValueError: If a variable holds a value that cannot be used.
    """
    prefix = f"{tracker.upper().replace('-', '_')}_"
    changes: dict[str, object] = {}

    raw_windows = os.environ.get(f"{prefix}RATE_LIMIT", "").strip()
    if raw_windows:
        changes["windows"] = _parse_windows(raw_windows, f"{prefix}RATE_LIMIT")

    overrides = {
        "min_interval": ([f"{prefix}MIN_INTERVAL"], float, 0.0),
        "max_retries": ([f"{prefix}MAX_RETRIES", "MAX_RETRIES"], int, 0),
        "backoff_initial": ([f"{prefix}BACKOFF_INITIAL", "BACKOFF_INITIAL"], float, 0.0),
        "backoff_max": ([f"{prefix}BACKOFF_MAX", "BACKOFF_MAX"], float, 0.0),
        "max_wait": ([f"{prefix}MAX_WAIT", "RATE_LIMIT_MAX_WAIT"], float, 0.0),
        "max_downloads": ([f"{prefix}MAX_DOWNLOADS_PER_RUN", "MAX_DOWNLOADS_PER_RUN"], int, 0),
    }
    for field, (names, convert, minimum) in overrides.items():
        value = _read_number(names, convert, minimum)
        if value is not None:
            changes[field] = value

    return replace(default, **changes) if changes else default
