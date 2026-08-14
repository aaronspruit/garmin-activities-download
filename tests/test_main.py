"""Tests for the main entry point and exit codes."""

from unittest.mock import MagicMock, patch

import pytest

from src.config import Config, DownloadTarget
from src.main import main
from src.trackers.base import TrackerAuthError, UnsafeActivityIdError


def _config(trackers=("garmin",)):
    return Config(
        trackers=list(trackers),
        tokens_dir="/tmp/tokens",
        output_dir="/tmp/data",
        days_back=7,
        download_targets=[DownloadTarget("FIT")],
    )


@pytest.fixture
def registry():
    """Patch the registry so no real tracker or network is involved."""
    with patch.dict("src.main.TRACKER_CLASSES", {"garmin": MagicMock(), "wahoo": MagicMock()}, clear=True) as reg:
        yield reg


class TestMain:
    @patch("src.main.download_new_activities", return_value=3)
    @patch("src.main.load_config")
    def test_exits_zero_on_success(self, mock_config, mock_download, registry):
        mock_config.return_value = _config()

        result = main()

        assert result == 0
        tracker = registry["garmin"].from_env.return_value
        registry["garmin"].from_env.assert_called_once_with("/tmp/tokens")
        tracker.authenticate.assert_called_once()
        mock_download.assert_called_once_with(
            tracker,
            output_dir="/tmp/data",
            days_back=7,
            targets=[DownloadTarget("FIT")],
        )

    @patch("src.main.download_new_activities", return_value=0)
    @patch("src.main.load_config")
    def test_exits_zero_when_no_new_activities(self, mock_config, mock_download, registry):
        mock_config.return_value = _config()

        assert main() == 0

    @patch("src.main.download_new_activities")
    @patch("src.main.load_config")
    def test_exits_one_on_auth_failure(self, mock_config, mock_download, registry):
        mock_config.return_value = _config()
        registry["garmin"].from_env.return_value.authenticate.side_effect = TrackerAuthError("No tokens")

        assert main() == 1

    @patch("src.main.download_new_activities")
    @patch("src.main.load_config")
    def test_exits_two_on_other_error(self, mock_config, mock_download, registry):
        mock_config.return_value = _config()
        mock_download.side_effect = RuntimeError("Network error")

        assert main() == 2

    @patch("src.main.download_new_activities")
    @patch("src.main.load_config")
    def test_exits_three_on_unsafe_activity_id(self, mock_config, mock_download, registry):
        mock_config.return_value = _config()
        mock_download.side_effect = UnsafeActivityIdError("non-alphanumeric activity id '../escape'")

        assert main() == 3

    @patch("src.main.load_config", side_effect=ValueError("Invalid TRACKERS entry 'polar'"))
    def test_exits_two_on_invalid_config(self, mock_config):
        assert main() == 2


class TestMultipleTrackers:
    """One tracker must never cost another its activities."""

    @patch("src.main.download_new_activities", return_value=2)
    @patch("src.main.load_config")
    def test_runs_every_tracker_and_sums_the_downloads(self, mock_config, mock_download, registry):
        mock_config.return_value = _config(["garmin", "wahoo"])

        assert main() == 0
        assert mock_download.call_count == 2
        registry["garmin"].from_env.assert_called_once_with("/tmp/tokens")
        registry["wahoo"].from_env.assert_called_once_with("/tmp/tokens")

    @patch("src.main.download_new_activities", return_value=1)
    @patch("src.main.load_config")
    def test_one_tracker_failing_does_not_stop_the_others(self, mock_config, mock_download, registry):
        mock_config.return_value = _config(["wahoo", "garmin"])
        registry["wahoo"].from_env.side_effect = TrackerAuthError("Wahoo tokens expired")

        result = main()

        # Wahoo failed, but Garmin still ran and the run reports the failure.
        assert result == 1
        assert mock_download.call_count == 1
        registry["garmin"].from_env.return_value.authenticate.assert_called_once()

    @patch("src.main.download_new_activities")
    @patch("src.main.load_config")
    def test_reports_the_most_severe_failure(self, mock_config, mock_download, registry):
        """An unsafe id (3) outranks a transient error (2), so it is not hidden."""
        mock_config.return_value = _config(["garmin", "wahoo"])
        mock_download.side_effect = [
            RuntimeError("transient"),
            UnsafeActivityIdError("non-alphanumeric activity id"),
        ]

        assert main() == 3

    @patch("src.main.download_new_activities")
    @patch("src.main.load_config")
    def test_auth_failure_outranks_a_transient_error(self, mock_config, mock_download, registry):
        mock_config.return_value = _config(["garmin", "wahoo"])
        mock_download.side_effect = [RuntimeError("transient"), TrackerAuthError("expired")]

        assert main() == 1

    @patch("src.main.download_new_activities", return_value=1)
    @patch("src.main.load_config")
    def test_success_of_one_does_not_mask_a_failure_of_another(self, mock_config, mock_download, registry):
        mock_config.return_value = _config(["garmin", "wahoo"])
        mock_download.side_effect = [1, RuntimeError("boom")]

        assert main() == 2
