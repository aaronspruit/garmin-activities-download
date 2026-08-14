"""Tests for the Garmin Connect tracker."""

from unittest.mock import MagicMock, patch

import pytest
from garminconnect import Garmin, GarminConnectAuthenticationError

from src.trackers.base import ActivityDownloadError, TrackerAuthError
from src.trackers.garmin import GarminTracker
from tests.conftest import (
    SAMPLE_ACTIVITY,
    SAMPLE_ACTIVITY_2,
    SAMPLE_EMPTY_ZIP,
    SAMPLE_FIT_CONTENT,
    SAMPLE_FIT_ZIP,
    SAMPLE_GPX,
)


class TestFromEnv:
    def test_reads_credentials_and_nests_tokenstore_under_the_tracker_name(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "test@example.com")
        monkeypatch.setenv("GARMIN_PASSWORD", "pass")

        tracker = GarminTracker.from_env("/app/tokens")

        assert tracker.email == "test@example.com"
        assert tracker.password == "pass"
        # Nested per tracker so two trackers never share a token store.
        assert tracker.tokenstore == "/app/tokens/garmin"

    def test_credentials_are_optional(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)
        monkeypatch.delenv("GARMIN_PASSWORD", raising=False)

        tracker = GarminTracker.from_env("/app/tokens")

        assert tracker.email is None
        assert tracker.password is None


class TestAuthenticate:
    def test_succeeds_with_saved_tokens(self):
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            mock_instance = MagicMock()
            mock_garmin_cls.return_value = mock_instance

            tracker = GarminTracker(tokenstore="/tmp/tokens")
            tracker.authenticate()

            mock_instance.login.assert_called_once_with("/tmp/tokens")
            assert tracker.client is mock_instance

    def test_falls_back_to_credentials(self):
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            token_instance = MagicMock()
            token_instance.login.side_effect = FileNotFoundError("No tokens")
            cred_instance = MagicMock()
            mock_garmin_cls.side_effect = [token_instance, cred_instance]

            tracker = GarminTracker(tokenstore="/tmp/tokens", email="test@example.com", password="pass")
            tracker.authenticate()

            assert tracker.client is cred_instance
            cred_instance.login.assert_called_once_with("/tmp/tokens")

    def test_raises_when_no_tokens_and_no_credentials(self):
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            mock_instance = MagicMock()
            mock_instance.login.side_effect = FileNotFoundError("No tokens")
            mock_garmin_cls.return_value = mock_instance

            tracker = GarminTracker(tokenstore="/tmp/tokens")

            with pytest.raises(TrackerAuthError, match="No valid tokens"):
                tracker.authenticate()

    def test_raises_when_credential_login_fails(self):
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            token_instance = MagicMock()
            token_instance.login.side_effect = FileNotFoundError
            cred_instance = MagicMock()
            cred_instance.login.side_effect = GarminConnectAuthenticationError("401")
            mock_garmin_cls.side_effect = [token_instance, cred_instance]

            tracker = GarminTracker(tokenstore="/tmp/tokens", email="test@example.com", password="wrong")

            with pytest.raises(GarminConnectAuthenticationError):
                tracker.authenticate()

    def test_raises_when_used_before_authenticating(self):
        tracker = GarminTracker(tokenstore="/tmp/tokens")

        with pytest.raises(TrackerAuthError, match="before authenticate"):
            tracker.list_activities("2026-08-01", "2026-08-08")


class TestListActivities:
    def _tracker(self, mock_garmin):
        tracker = GarminTracker(tokenstore="/tmp/tokens")
        tracker.client = mock_garmin
        return tracker

    def test_normalizes_garmin_response(self, mock_garmin):
        mock_garmin.get_activities_by_date.return_value = [SAMPLE_ACTIVITY, SAMPLE_ACTIVITY_2]

        activities = self._tracker(mock_garmin).list_activities("2026-07-14", "2026-07-21")

        mock_garmin.get_activities_by_date.assert_called_once_with("2026-07-14", "2026-07-21")
        assert [(a.id, a.name) for a in activities] == [
            ("19876543210", "Morning Run"),
            ("19876543211", "Evening Ride"),
        ]

    def test_ids_are_stringified(self, mock_garmin):
        """Garmin returns ints; the downloader builds paths, so it needs text."""
        activities = self._tracker(mock_garmin).list_activities("2026-07-14", "2026-07-21")

        assert isinstance(activities[0].id, str)

    def test_defaults_missing_name(self, mock_garmin):
        mock_garmin.get_activities_by_date.return_value = [{"activityId": 42}]

        activities = self._tracker(mock_garmin).list_activities("2026-07-14", "2026-07-21")

        assert activities[0].name == "Unknown"


class TestDownload:
    def _tracker(self, mock_garmin):
        tracker = GarminTracker(tokenstore="/tmp/tokens")
        tracker.client = mock_garmin
        return tracker

    def test_requests_the_matching_garmin_format(self, mock_garmin):
        activity = self._tracker(mock_garmin).list_activities("2026-07-14", "2026-07-21")[0]

        self._tracker(mock_garmin).download(activity, "GPX")

        mock_garmin.download_activity.assert_called_with(
            "19876543210",
            dl_fmt=Garmin.ActivityDownloadFormat.GPX,
        )

    def test_returns_unwrapped_bytes_for_unzipped_formats(self, mock_garmin):
        activity = self._tracker(mock_garmin).list_activities("2026-07-14", "2026-07-21")[0]

        assert self._tracker(mock_garmin).download(activity, "GPX") == SAMPLE_GPX

    def test_fit_is_requested_as_original_and_extracted_from_the_zip(self, mock_garmin):
        """Garmin has no FIT format: it serves FIT inside an ORIGINAL-format zip."""
        mock_garmin.download_activity.return_value = SAMPLE_FIT_ZIP
        tracker = self._tracker(mock_garmin)
        activity = tracker.list_activities("2026-07-14", "2026-07-21")[0]

        data = tracker.download(activity, "FIT")

        mock_garmin.download_activity.assert_called_with(
            "19876543210",
            dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL,
        )
        assert data == SAMPLE_FIT_CONTENT

    def test_raises_when_zip_has_no_fit_member(self, mock_garmin):
        mock_garmin.download_activity.return_value = SAMPLE_EMPTY_ZIP
        tracker = self._tracker(mock_garmin)
        activity = tracker.list_activities("2026-07-14", "2026-07-21")[0]

        with pytest.raises(ActivityDownloadError, match="no .fit file"):
            tracker.download(activity, "FIT")


class TestSupportedFormats:
    def test_supports_all_three_formats(self):
        assert GarminTracker.supported_formats == {"FIT", "GPX", "TCX"}
