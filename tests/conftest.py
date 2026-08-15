"""Shared test fixtures."""

import io
import zipfile
from unittest.mock import MagicMock

import garminconnect
import pytest

from src.ratelimit import RateLimitPolicy
from src.trackers import TRACKER_CLASSES
from src.trackers.base import Activity, Tracker

SAMPLE_ACTIVITY = {
    "activityId": 19876543210,
    "activityName": "Morning Run",
    "startTimeLocal": "2026-07-20 06:30:00",
    "activityType": {"typeId": 1, "typeKey": "running"},
    "duration": 2400.0,
    "distance": 6200.0,
}

SAMPLE_ACTIVITY_2 = {
    "activityId": 19876543211,
    "activityName": "Evening Ride",
    "startTimeLocal": "2026-07-20 17:00:00",
    "activityType": {"typeId": 2, "typeKey": "cycling"},
    "duration": 3600.0,
    "distance": 25000.0,
}

# The normalized form the downloader works with, independent of any tracker.
ACTIVITY = Activity(id="19876543210", name="Morning Run")
ACTIVITY_2 = Activity(id="19876543211", name="Evening Ride")

SAMPLE_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Garmin Connect"
     xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>Morning Run</name>
    <trkseg>
      <trkpt lat="40.7128" lon="-74.0060">
        <ele>10</ele>
        <time>2026-07-20T06:30:00Z</time>
      </trkpt>
    </trkseg>
  </trk>
</gpx>"""

SAMPLE_TCX = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-07-20T06:30:00Z</Id>
      <Lap StartTime="2026-07-20T06:30:00Z">
        <TotalTimeSeconds>2400.0</TotalTimeSeconds>
        <DistanceMeters>6200.0</DistanceMeters>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>"""

SAMPLE_FIT_CONTENT = b"\x0e\x10FAKE_FIT_BINARY_CONTENT"


def _build_zip(members: dict[str, bytes]) -> bytes:
    """Build in-memory zip bytes containing the given name -> content members."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buffer.getvalue()


SAMPLE_FIT_ZIP = _build_zip(
    {
        "manifest.json": b"{}",
        "19876543210_ACTIVITY.fit": SAMPLE_FIT_CONTENT,
    }
)

SAMPLE_EMPTY_ZIP = _build_zip({"manifest.json": b"{}"})


# --- Wahoo samples --------------------------------------------------------

WAHOO_FIT_CONTENT = b"\x0e\x10WAHOO_FIT_BINARY"

SAMPLE_WAHOO_WORKOUT = {
    "id": 4471,
    "name": "Morning Ride",
    "starts": "2026-08-12T06:00:00.000Z",
    "minutes": 62,
    "workout_summary": {
        "distance_accum": "25000.0",
        "file": {"url": "https://cdn.wahooligan.com/workouts/4471.fit"},
    },
}

# A planned or in-progress workout: no recorded file, so nothing to download.
SAMPLE_WAHOO_WORKOUT_NO_FILE = {
    "id": 4472,
    "name": "Planned Ride",
    "starts": "2026-08-12T07:00:00.000Z",
    "minutes": 0,
}

SAMPLE_WAHOO_TOKENS = {
    "access_token": "saved-access-token",
    "refresh_token": "saved-refresh-token",
    "expires_at": 0,  # Expired, so a refresh is due.
}

SAMPLE_WAHOO_TOKEN_RESPONSE = {
    "access_token": "rotated-access-token",
    "refresh_token": "rotated-refresh-token",
    "expires_in": 7200,
}


# --- Tracker fixtures -----------------------------------------------------


class FakeTracker(Tracker):
    """A concrete tracker whose network calls are mocks.

    Deliberately a real `Tracker` subclass rather than `MagicMock(spec=Tracker)`:
    it keeps the downloader tests honest about the interface, and `name` and
    `supported_formats` are bare annotations on the ABC, so a spec'd mock would
    not carry them.
    """

    name = "fake"
    supported_formats = frozenset({"FIT", "GPX", "TCX"})
    # No windows and no spacing, so a downloader test never waits. A test that
    # needs a limit passes its own policy.
    rate_limit = RateLimitPolicy(windows=(), min_interval=0.0)

    def __init__(self, activities=None, data=SAMPLE_GPX, rate_limit=None):
        # Instance attributes shadow the methods below, so tests can assert on
        # call counts and arguments exactly as they would with a MagicMock.
        self.list_activities = MagicMock(return_value=list(activities if activities is not None else [ACTIVITY]))
        self.download = MagicMock(return_value=data)
        if rate_limit is not None:
            self.rate_limit = rate_limit

    @classmethod
    def from_env(cls, tokens_dir):
        return cls()

    def authenticate(self):
        pass

    def list_activities(self, start_date, end_date):  # pragma: no cover - replaced per instance
        raise NotImplementedError

    def download(self, activity, fmt):  # pragma: no cover - replaced per instance
        raise NotImplementedError

    @classmethod
    def interactive_setup(cls, tokens_dir):  # pragma: no cover - not used in tests
        raise NotImplementedError


# Names that `load_policy` reads. The shared forms are generic enough to exist
# already in a developer shell or a devcontainer `.env`, and a leaked value
# changes a policy that a test asserts on.
_RATE_LIMIT_VARS = (
    "MAX_DOWNLOADS_PER_RUN",
    "MAX_RETRIES",
    "BACKOFF_INITIAL",
    "BACKOFF_MAX",
    "RATE_LIMIT_MAX_WAIT",
)
# The per-tracker form of `RATE_LIMIT_MAX_WAIT` is `<TRACKER>_MAX_WAIT`, so the
# shared name is dropped and `MAX_WAIT` is added below.
_TRACKER_SUFFIXES = _RATE_LIMIT_VARS[:-1] + ("RATE_LIMIT", "MIN_INTERVAL", "MAX_WAIT")

# The exact names that `load_policy` reads, and nothing else. A predicate on the
# suffix alone would also delete `DATABASE_MAX_WAIT` and its like.
_RATE_LIMIT_ENV = set(_RATE_LIMIT_VARS) | {
    f"{name.upper().replace('-', '_')}_{suffix}" for name in TRACKER_CLASSES for suffix in _TRACKER_SUFFIXES
}


@pytest.fixture(autouse=True)
def clean_rate_limit_env(monkeypatch):
    """Hide any rate limit variable of the real environment from the tests.

    The shared names are generic enough to exist already in a developer shell or
    in a devcontainer `.env`, and a leaked value changes a policy that a test
    asserts on. A test that wants one sets it with `monkeypatch.setenv`.
    """
    for name in _RATE_LIMIT_ENV:
        monkeypatch.delenv(name, raising=False)


class FakeTime:
    """A clock that only moves when something sleeps.

    `src.ratelimit` reads `time.sleep` and `time.monotonic` at each call, so
    replacing the whole module reference reaches a limiter that already exists.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def fake_time(monkeypatch):
    """Make every rate limit wait instant, and still let the clock pass.

    The retries and the pacing stay real. Only the waiting is removed. A no-op
    `sleep` with a real clock would leave the time unchanged after a wait, so a
    limiter would admit more requests than its windows allow and no test could
    see it.
    """
    clock = FakeTime()
    monkeypatch.setattr("src.ratelimit.time", clock)
    return clock


@pytest.fixture
def mock_tracker():
    """Tracker mock with default happy-path responses."""
    return FakeTracker()


@pytest.fixture
def mock_garmin():
    """Garmin client mock with default happy-path responses."""
    garmin = MagicMock(spec=garminconnect.Garmin)
    garmin.get_activities_by_date.return_value = [SAMPLE_ACTIVITY]
    garmin.download_activity.return_value = SAMPLE_GPX
    garmin.ActivityDownloadFormat = garminconnect.Garmin.ActivityDownloadFormat
    return garmin
