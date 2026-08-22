"""Tests for the interactive setup entry point and per-tracker setup flows."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.setup import setup
from src.trackers.wahoo import WahooTracker


class TestSetupDispatch:
    def test_dispatches_to_the_named_tracker(self, monkeypatch):
        monkeypatch.setenv("TOKENS_DIR", "/custom/tokens")
        tracker = MagicMock()

        with patch.dict("src.setup.TRACKER_CLASSES", {"garmin": tracker}, clear=True):
            setup(["garmin"])

        tracker.interactive_setup.assert_called_once_with("/custom/tokens")

    def test_tracker_name_is_case_insensitive(self, monkeypatch):
        monkeypatch.delenv("TOKENS_DIR", raising=False)
        tracker = MagicMock()

        with patch.dict("src.setup.TRACKER_CLASSES", {"wahoo": tracker}, clear=True):
            setup([" WAHOO "])

        tracker.interactive_setup.assert_called_once_with("/app/tokens")

    @pytest.mark.parametrize("argv", [[], ["garmin", "wahoo"]])
    def test_requires_exactly_one_tracker_name(self, argv, capsys):
        with pytest.raises(SystemExit) as exc:
            setup(argv)

        assert exc.value.code == 1
        assert "Usage: python -m src.setup <tracker>" in capsys.readouterr().err

    def test_rejects_an_unknown_tracker_and_lists_the_valid_ones(self, capsys):
        with pytest.raises(SystemExit) as exc:
            setup(["strava"])

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Unknown tracker 'strava'" in err
        assert "garmin, polar, wahoo" in err


class TestGarminInteractiveSetup:
    def test_saves_tokens_under_the_tracker_folder(self, monkeypatch, capsys):
        from src.trackers.garmin import GarminTracker

        monkeypatch.setattr("builtins.input", MagicMock(side_effect=["me@example.com", "pass"]))

        with patch("src.trackers.garmin.Garmin") as mock_garmin_cls:
            GarminTracker.interactive_setup("/app/tokens")

        client = mock_garmin_cls.return_value
        client.login.assert_called_once_with("/app/tokens/garmin")
        assert mock_garmin_cls.call_args.kwargs["email"] == "me@example.com"
        # MFA is prompted for interactively, which is the point of this script.
        assert callable(mock_garmin_cls.call_args.kwargs["prompt_mfa"])
        assert "/app/tokens/garmin" in capsys.readouterr().out

    @pytest.mark.parametrize("answers", [["", "pass"], ["me@example.com", ""]])
    def test_requires_both_credentials(self, monkeypatch, answers, capsys):
        from src.trackers.garmin import GarminTracker

        monkeypatch.setattr("builtins.input", MagicMock(side_effect=answers))

        with pytest.raises(SystemExit) as exc:
            GarminTracker.interactive_setup("/app/tokens")

        assert exc.value.code == 1
        assert "required" in capsys.readouterr().err


class TestWahooInteractiveSetup:
    def _env(self, monkeypatch):
        monkeypatch.setenv("WAHOO_CLIENT_ID", "client-id")
        monkeypatch.setenv("WAHOO_CLIENT_SECRET", "client-secret")
        monkeypatch.delenv("WAHOO_REDIRECT_URI", raising=False)

    def test_exchanges_the_pasted_code_and_saves_tokens(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value=" pasted-code "))
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 7200,
        }

        with patch("src.trackers.wahoo.requests.post", return_value=response) as post:
            WahooTracker.interactive_setup(str(tmp_path))

        sent = post.call_args.kwargs["data"]
        assert sent["grant_type"] == "authorization_code"
        assert sent["code"] == "pasted-code"
        assert sent["redirect_uri"] == "https://localhost"

        saved = json.loads((tmp_path / "wahoo" / "tokens.json").read_text())
        assert saved["refresh_token"] == "new-refresh"
        assert saved["expires_at"] > 0

    def test_prints_an_authorize_url_with_the_read_scopes(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value="code"))
        response = MagicMock(status_code=200)
        response.json.return_value = {"access_token": "a", "refresh_token": "r", "expires_in": 7200}

        with patch("src.trackers.wahoo.requests.post", return_value=response):
            WahooTracker.interactive_setup(str(tmp_path))

        out = capsys.readouterr().out
        assert "https://api.wahooligan.com/oauth/authorize?client_id=client-id" in out
        assert "workouts_read" in out
        assert "offline_data" in out
        # The failing redirect is expected, so the operator must be told.
        assert "expected" in out

    def test_warns_that_saved_tokens_do_not_mean_an_approved_application(self, monkeypatch, tmp_path, capsys):
        """Setup succeeds either way, so the 422 that follows must be explained here."""
        self._env(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value="code"))
        response = MagicMock(status_code=200)
        response.json.return_value = {"access_token": "a", "refresh_token": "r", "expires_in": 7200}

        with patch("src.trackers.wahoo.requests.post", return_value=response):
            WahooTracker.interactive_setup(str(tmp_path))

        out = capsys.readouterr().out
        assert "has not been approved" in out
        assert "https://developer.wahoo.com/applications" in out

    def test_honours_a_custom_redirect_uri(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setenv("WAHOO_REDIRECT_URI", "https://example.com/cb")
        monkeypatch.setattr("builtins.input", MagicMock(return_value="code"))
        response = MagicMock(status_code=200)
        response.json.return_value = {"access_token": "a", "refresh_token": "r", "expires_in": 7200}

        with patch("src.trackers.wahoo.requests.post", return_value=response) as post:
            WahooTracker.interactive_setup(str(tmp_path))

        assert post.call_args.kwargs["data"]["redirect_uri"] == "https://example.com/cb"
        assert "https://example.com/cb" in capsys.readouterr().out

    @pytest.mark.parametrize("missing", ["WAHOO_CLIENT_ID", "WAHOO_CLIENT_SECRET"])
    def test_requires_the_developer_app_credentials(self, monkeypatch, tmp_path, missing, capsys):
        self._env(monkeypatch)
        monkeypatch.delenv(missing)

        with pytest.raises(SystemExit) as exc:
            WahooTracker.interactive_setup(str(tmp_path))

        assert exc.value.code == 1
        assert "Wahoo developer portal" in capsys.readouterr().err

    def test_requires_a_code(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value="  "))

        with pytest.raises(SystemExit) as exc:
            WahooTracker.interactive_setup(str(tmp_path))

        assert exc.value.code == 1
        assert "authorization code is required" in capsys.readouterr().err

    def test_reports_a_rejected_exchange_without_writing_tokens(self, monkeypatch, tmp_path, capsys):
        self._env(monkeypatch)
        monkeypatch.setattr("builtins.input", MagicMock(return_value="stale-code"))
        response = MagicMock(status_code=400, text="invalid_grant")

        with patch("src.trackers.wahoo.requests.post", return_value=response):
            with pytest.raises(SystemExit) as exc:
                WahooTracker.interactive_setup(str(tmp_path))

        assert exc.value.code == 1
        assert "invalid_grant" in capsys.readouterr().err
        assert not (tmp_path / "wahoo" / "tokens.json").exists()
