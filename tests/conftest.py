"""Shared test fixtures."""

from unittest.mock import MagicMock

import garminconnect
import pytest

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


@pytest.fixture
def mock_garmin():
    """Garmin client mock with default happy-path responses."""
    garmin = MagicMock(spec=garminconnect.Garmin)
    garmin.get_activities_by_date.return_value = [SAMPLE_ACTIVITY]
    garmin.download_activity.return_value = SAMPLE_GPX
    garmin.ActivityDownloadFormat = garminconnect.Garmin.ActivityDownloadFormat
    return garmin
