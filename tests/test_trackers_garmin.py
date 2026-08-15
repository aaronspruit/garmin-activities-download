"""Tests for the Garmin Connect tracker."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

from src.ratelimit import RateLimitError, Window
from src.trackers.base import Activity, ActivityDownloadError, TrackerAuthError
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


class TestRateLimit:
    """Garmin publishes no limits, so the defaults are a conservative guess."""

    def _ready(self, mock_garmin):
        tracker = GarminTracker(tokenstore="/tmp/tokens")
        tracker.client = mock_garmin
        return tracker

    def test_the_default_is_slow(self):
        assert GarminTracker.rate_limit.windows == (Window(20, 60), Window(300, 3600), Window(2000, 86400))
        assert GarminTracker.rate_limit.min_interval == 2.0

    def test_an_environment_variable_replaces_it(self, monkeypatch):
        monkeypatch.setenv("GARMIN_RATE_LIMIT", "60/60")
        monkeypatch.setenv("GARMIN_MIN_INTERVAL", "0.5")

        tracker = GarminTracker.from_env("/app/tokens")

        assert tracker.limiter.policy.windows == (Window(60, 60.0),)
        assert tracker.limiter.policy.min_interval == 0.5

    def test_listing_spends_the_budget(self, mock_garmin):
        tracker = self._ready(mock_garmin)

        tracker.list_activities("2026-07-01", "2026-07-20")

        assert tracker.limiter.requests == 1

    def test_every_download_spends_the_budget(self, mock_garmin):
        """Unlike Wahoo, Garmin serves the files itself and counts them."""
        tracker = self._ready(mock_garmin)

        tracker.download(Activity(id="1", name="Run"), "GPX")
        tracker.download(Activity(id="2", name="Ride"), "GPX")

        assert tracker.limiter.requests == 2

    def test_a_rate_limit_refusal_is_retried(self, mock_garmin):
        tracker = self._ready(mock_garmin)
        mock_garmin.get_activities_by_date.side_effect = [
            GarminConnectTooManyRequestsError("429"),
            [SAMPLE_ACTIVITY],
        ]

        activities = tracker.list_activities("2026-07-01", "2026-07-20")

        assert [a.id for a in activities] == ["19876543210"]

    def test_it_stops_after_the_last_retry(self, mock_garmin):
        tracker = self._ready(mock_garmin)
        mock_garmin.download_activity.side_effect = GarminConnectTooManyRequestsError("429")

        with pytest.raises(RateLimitError, match="rate limit"):
            tracker.download(Activity(id="1", name="Run"), "GPX")

        # The first attempt, then the two retries that the policy allows.
        assert mock_garmin.download_activity.call_count == 3

    def test_a_login_refusal_does_not_try_the_credentials(self):
        """A second login after a 429 only makes the account lockout longer."""
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            instance = MagicMock()
            instance.login.side_effect = GarminConnectTooManyRequestsError("429")
            mock_garmin_cls.return_value = instance

            tracker = GarminTracker(tokenstore="/tmp/tokens", email="a@b.c", password="pass")

            with pytest.raises(TrackerAuthError, match="rate limit"):
                tracker.authenticate()

            # The first attempt and the two retries that the policy allows, all
            # on the same client. No second client for the credential fallback.
            assert instance.login.call_count == 3
            assert mock_garmin_cls.call_count == 1

    def test_a_budget_stop_does_not_try_the_credentials(self):
        """`BudgetExhaustedError` is the other rate limit outcome of a login."""
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            instance = MagicMock()
            instance.login.side_effect = GarminConnectTooManyRequestsError("429")
            mock_garmin_cls.return_value = instance

            tracker = GarminTracker(tokenstore="/tmp/tokens", email="a@b.c", password="pass")
            # A wait longer than the run accepts, which `_attempt` reports as a
            # budget stop rather than as a rate limit error.
            tracker.rate_limit = replace(tracker.rate_limit, max_wait=1.0, backoff_initial=600.0)

            with pytest.raises(TrackerAuthError, match="Do not log in again"):
                tracker.authenticate()

            assert instance.login.call_count == 1
            assert mock_garmin_cls.call_count == 1

    def test_a_refusal_of_the_credential_login_is_named(self):
        """The fallback path must give the same guidance as the token path."""
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            token_client = MagicMock()
            token_client.login.side_effect = FileNotFoundError("No tokens")
            credential_client = MagicMock()
            credential_client.login.side_effect = GarminConnectTooManyRequestsError("429")
            mock_garmin_cls.side_effect = [token_client, credential_client]

            tracker = GarminTracker(tokenstore="/tmp/tokens", email="a@b.c", password="pass")

            with pytest.raises(TrackerAuthError, match="Do not log in again"):
                tracker.authenticate()

    def test_a_login_refusal_names_the_wait(self):
        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            instance = MagicMock()
            instance.login.side_effect = GarminConnectTooManyRequestsError("429")
            mock_garmin_cls.return_value = instance

            tracker = GarminTracker(tokenstore="/tmp/tokens")

            with pytest.raises(TrackerAuthError, match="Do not log in again"):
                tracker.authenticate()
