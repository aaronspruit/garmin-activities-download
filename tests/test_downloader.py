"""Tests for activity download and deduplication logic."""

import pytest
from garminconnect import GarminConnectTooManyRequestsError

from src.downloader import download_new_activities
from tests.conftest import SAMPLE_ACTIVITY, SAMPLE_ACTIVITY_2, SAMPLE_GPX


class TestDownloadNewActivities:
    def test_downloads_new_activity(self, mock_garmin, tmp_path):
        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 1
        gpx_file = tmp_path / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        assert gpx_file.exists()
        assert gpx_file.read_bytes() == SAMPLE_GPX

    def test_skips_existing_activity(self, mock_garmin, tmp_path):
        existing = tmp_path / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        existing.write_bytes(b"existing data")

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 0
        mock_garmin.download_activity.assert_not_called()
        assert existing.read_bytes() == b"existing data"

    def test_downloads_multiple_activities(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = [
            SAMPLE_ACTIVITY,
            SAMPLE_ACTIVITY_2,
        ]

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 2
        assert (tmp_path / f"{SAMPLE_ACTIVITY['activityId']}.gpx").exists()
        assert (tmp_path / f"{SAMPLE_ACTIVITY_2['activityId']}.gpx").exists()

    def test_skips_existing_downloads_new(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = [
            SAMPLE_ACTIVITY,
            SAMPLE_ACTIVITY_2,
        ]
        existing = tmp_path / f"{SAMPLE_ACTIVITY['activityId']}.gpx"
        existing.write_bytes(b"existing")

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 1
        assert mock_garmin.download_activity.call_count == 1

    def test_creates_output_directory(self, mock_garmin, tmp_path):
        output = tmp_path / "nested" / "dir"
        download_new_activities(mock_garmin, str(output), days_back=7, download_delay=0)

        assert output.exists()

    def test_handles_no_activities(self, mock_garmin, tmp_path):
        mock_garmin.get_activities_by_date.return_value = []

        count = download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        assert count == 0

    def test_propagates_rate_limit_error(self, mock_garmin, tmp_path):
        mock_garmin.download_activity.side_effect = GarminConnectTooManyRequestsError("429")

        with pytest.raises(GarminConnectTooManyRequestsError):
            download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

    def test_calls_download_with_gpx_format(self, mock_garmin, tmp_path):
        download_new_activities(mock_garmin, str(tmp_path), days_back=7, download_delay=0)

        mock_garmin.download_activity.assert_called_once_with(
            SAMPLE_ACTIVITY["activityId"],
            dl_fmt=mock_garmin.ActivityDownloadFormat.GPX,
        )
