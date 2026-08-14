"""Tests for environment and Docker-secret reading."""

from unittest.mock import mock_open, patch

from src.env import read_secret


class TestReadSecret:
    def test_prefers_the_docker_secret_file(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "from-env@example.com")

        with patch("builtins.open", mock_open(read_data="from-secret@example.com\n")) as opened:
            assert read_secret("GARMIN_EMAIL") == "from-secret@example.com"

        # The file name is the lowercased variable name.
        opened.assert_called_once_with("/run/secrets/garmin_email")

    def test_falls_back_to_the_env_var(self, monkeypatch):
        monkeypatch.setenv("GARMIN_EMAIL", "from-env@example.com")

        with patch("builtins.open", side_effect=FileNotFoundError):
            assert read_secret("GARMIN_EMAIL") == "from-env@example.com"

    def test_returns_none_when_neither_is_set(self, monkeypatch):
        monkeypatch.delenv("GARMIN_EMAIL", raising=False)

        with patch("builtins.open", side_effect=FileNotFoundError):
            assert read_secret("GARMIN_EMAIL") is None

    def test_works_for_any_tracker_prefix(self, monkeypatch):
        """Trackers read their own secrets, so the helper must not be Garmin-specific."""
        with patch("builtins.open", mock_open(read_data="wahoo-secret\n")) as opened:
            assert read_secret("WAHOO_CLIENT_SECRET") == "wahoo-secret"

        opened.assert_called_once_with("/run/secrets/wahoo_client_secret")
