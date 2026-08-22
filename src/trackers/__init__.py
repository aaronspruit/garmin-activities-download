"""Tracker registry.

To add a tracker: write `src/trackers/<name>.py` with a `Tracker` subclass, then
add the class to `_REGISTERED` below. Nothing else in the pipeline changes.

Keep every import here free of side effects. CI smoke-tests the built image with
`python -c "import src.main"`, which reaches this module.
"""

from src.trackers.base import (
    FORMAT_EXTENSIONS,
    Activity,
    ActivityDownloadError,
    Tracker,
    TrackerAuthError,
    UnsafeActivityIdError,
)
from src.trackers.garmin import GarminTracker
from src.trackers.polar import PolarTracker
from src.trackers.wahoo import WahooTracker

_REGISTERED: tuple[type[Tracker], ...] = (GarminTracker, WahooTracker, PolarTracker)

TRACKER_CLASSES: dict[str, type[Tracker]] = {tracker.name: tracker for tracker in _REGISTERED}

__all__ = [
    "FORMAT_EXTENSIONS",
    "TRACKER_CLASSES",
    "Activity",
    "ActivityDownloadError",
    "GarminTracker",
    "PolarTracker",
    "Tracker",
    "TrackerAuthError",
    "UnsafeActivityIdError",
    "WahooTracker",
]
