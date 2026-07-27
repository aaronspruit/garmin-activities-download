"""Tests for the main entry point and exit codes."""

from unittest.mock import MagicMock, patch

from src.auth import AuthenticationError
from src.main import main


class TestMain:
    @patch("src.main.download_new_activities", return_value=3)
    @patch("src.main.authenticate")
    @patch("src.main.load_config")
    def test_exits_zero_on_success(self, mock_config, mock_auth, mock_download):
        mock_config.return_value = MagicMock(
            email="test@example.com",
            password="pass",
            tokenstore="/tmp/tokens",
            output_dir="/tmp/data",
            days_back=7,
            download_formats=["FIT"],
        )

        result = main()

        assert result == 0
        mock_download.assert_called_once_with(
            mock_auth.return_value,
            output_dir="/tmp/data",
            days_back=7,
            formats=["FIT"],
        )

    @patch("src.main.authenticate")
    @patch("src.main.load_config")
    def test_exits_one_on_auth_failure(self, mock_config, mock_auth):
        mock_config.return_value = MagicMock()
        mock_auth.side_effect = AuthenticationError("No tokens")

        result = main()

        assert result == 1

    @patch("src.main.download_new_activities")
    @patch("src.main.authenticate")
    @patch("src.main.load_config")
    def test_exits_two_on_other_error(self, mock_config, mock_auth, mock_download):
        mock_config.return_value = MagicMock()
        mock_auth.return_value = MagicMock()
        mock_download.side_effect = RuntimeError("Network error")

        result = main()

        assert result == 2

    @patch("src.main.download_new_activities", return_value=0)
    @patch("src.main.authenticate")
    @patch("src.main.load_config")
    def test_exits_zero_when_no_new_activities(self, mock_config, mock_auth, mock_download):
        mock_config.return_value = MagicMock(
            email=None,
            password=None,
            tokenstore="/tmp/tokens",
            output_dir="/tmp/data",
            days_back=7,
            download_formats=["FIT"],
        )

        result = main()

        assert result == 0
