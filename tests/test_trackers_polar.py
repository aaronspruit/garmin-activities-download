"""Tests for the Polar AccessLink tracker.

Polar has no refresh flow, unlike Wahoo, so these tests pin the simpler
authenticate() and the 30-day-window listing, rather than a token lifecycle.
"""

import json
from unittest.mock import MagicMock

import pytest
import requests

from src.ratelimit import RateLimitError, TransientError
from src.trackers.base import Activity, ActivityDownloadError, TrackerAuthError
from src.trackers.polar import EXERCISES_URL, USERS_URL, PolarTracker
from tests.conftest import (
    POLAR_FIT_CONTENT,
    POLAR_GPX_CONTENT,
    POLAR_TCX_CONTENT,
    POLAR_TCX_CONTENT_GZIPPED,
    SAMPLE_POLAR_EXERCISE,
    SAMPLE_POLAR_EXERCISE_PLAIN,
    SAMPLE_POLAR_TOKEN_RESPONSE,
    SAMPLE_POLAR_TOKENS,
)


def _response(status_code=200, json_body=None, content=b"", headers=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.content = content
    response.text = text
    response.headers = headers if headers is not None else {}
    return response


def _write_tokens(tmp_path, tokens=None):
    path = tmp_path / "polar" / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens if tokens is not None else SAMPLE_POLAR_TOKENS))
    return path


def _tracker(tmp_path):
    tracker = PolarTracker(token_path=str(tmp_path / "polar" / "tokens.json"))
    tracker.session = MagicMock()
    return tracker


class TestFromEnv:
    def test_needs_no_credentials(self, tmp_path):
        """Unlike Wahoo, nothing is refreshed at runtime, so headless mode needs no client secret."""
        tracker = PolarTracker.from_env("/app/tokens")

        assert tracker.token_path == "/app/tokens/polar/tokens.json"


class TestAuthenticate:
    def test_makes_no_network_call(self, tmp_path):
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)

        tracker.authenticate()

        tracker.session.get.assert_not_called()

    def test_raises_when_token_file_is_missing(self, tmp_path):
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="python -m src.setup polar"):
            tracker.authenticate()

    def test_raises_when_token_file_is_corrupt(self, tmp_path):
        path = tmp_path / "polar" / "tokens.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="could not be read"):
            tracker.authenticate()

    def test_raises_when_access_token_is_absent(self, tmp_path):
        _write_tokens(tmp_path, {"something_else": "x"})
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="no access_token"):
            tracker.authenticate()

    def test_raises_when_used_before_authenticating(self, tmp_path):
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="before authenticate"):
            tracker.list_activities("2026-08-06", "2026-08-13")


class TestListActivities:
    def _ready(self, tmp_path):
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_normalizes_exercises_in_range(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body=[SAMPLE_POLAR_EXERCISE])

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert activities == [Activity(id="2AC312F", name="RUNNING")]
        assert tracker.session.get.call_args.args[0] == EXERCISES_URL

    def test_sends_the_bearer_token(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body=[])

        tracker.list_activities("2026-08-06", "2026-08-13")

        headers = tracker.session.get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer saved-polar-access-token"

    def test_falls_back_to_the_plain_sport_when_no_detail_is_given(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body=[SAMPLE_POLAR_EXERCISE_PLAIN])

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert activities == [Activity(id="9F00A1", name="OTHER")]

    def test_filters_out_exercises_outside_the_range(self, tmp_path):
        tracker = self._ready(tmp_path)
        too_old = {**SAMPLE_POLAR_EXERCISE, "id": "OLD", "start_time": "2026-08-01T06:00:00"}
        tracker.session.get.return_value = _response(json_body=[SAMPLE_POLAR_EXERCISE, too_old])

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [a.id for a in activities] == ["2AC312F"]

    def test_skips_exercises_with_no_id(self, tmp_path):
        tracker = self._ready(tmp_path)
        no_id = {**SAMPLE_POLAR_EXERCISE, "id": None}
        tracker.session.get.return_value = _response(json_body=[no_id])

        assert tracker.list_activities("2026-08-06", "2026-08-13") == []

    def test_an_empty_list_gives_no_activities(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body=[])

        assert tracker.list_activities("2026-08-06", "2026-08-13") == []


class TestDownload:
    def _ready(self, tmp_path):
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_fetches_fit_unchanged(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(content=POLAR_FIT_CONTENT)
        activity = Activity(id="2AC312F", name="RUNNING")

        data = tracker.download(activity, "FIT")

        assert data == POLAR_FIT_CONTENT
        assert tracker.session.get.call_args.args[0] == f"{EXERCISES_URL}/2AC312F/fit"

    def test_fetches_gpx_unchanged(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(content=POLAR_GPX_CONTENT)
        activity = Activity(id="2AC312F", name="RUNNING")

        data = tracker.download(activity, "GPX")

        assert data == POLAR_GPX_CONTENT
        assert tracker.session.get.call_args.args[0] == f"{EXERCISES_URL}/2AC312F/gpx"

    def test_decompresses_the_gzipped_tcx(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(content=POLAR_TCX_CONTENT_GZIPPED)
        activity = Activity(id="2AC312F", name="RUNNING")

        data = tracker.download(activity, "TCX")

        assert data == POLAR_TCX_CONTENT
        assert tracker.session.get.call_args.args[0] == f"{EXERCISES_URL}/2AC312F/tcx"

    def test_a_missing_exercise_is_a_download_error(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=404)
        activity = Activity(id="2AC312F", name="RUNNING")

        with pytest.raises(ActivityDownloadError, match="not found"):
            tracker.download(activity, "FIT")


class TestApiErrors:
    def _ready(self, tmp_path):
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_missing_consents_is_an_auth_error(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=403, text="Forbidden")

        with pytest.raises(TrackerAuthError, match="mandatory consent"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_a_rejected_token_is_an_auth_error(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=401, json_body={"error": "unauthorized"})

        with pytest.raises(TrackerAuthError, match="unauthorized"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_other_failures_keep_the_http_error_but_carry_the_body(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=400, json_body={"error": "bad request"})

        with pytest.raises(requests.HTTPError, match="bad request"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_a_server_failure_is_retried(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.side_effect = [
            _response(status_code=503, text="unavailable"),
            _response(json_body=[SAMPLE_POLAR_EXERCISE]),
        ]

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [a.id for a in activities] == ["2AC312F"]

    def test_a_429_is_retried(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.side_effect = [
            _response(status_code=429, json_body={"error": "too many requests"}),
            _response(json_body=[SAMPLE_POLAR_EXERCISE]),
        ]

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [a.id for a in activities] == ["2AC312F"]

    def test_a_429_raises_rate_limit_error_directly(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.rate_limit = tracker.rate_limit.__class__(windows=(), min_interval=0.0, max_retries=0)
        tracker.session.get.return_value = _response(status_code=429, json_body={"error": "slow down"})

        with pytest.raises(RateLimitError, match="rate limit"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_a_5xx_raises_transient_error_directly(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.rate_limit = tracker.rate_limit.__class__(windows=(), min_interval=0.0, max_retries=0)
        tracker.session.get.return_value = _response(status_code=502, text="Bad Gateway")

        with pytest.raises(TransientError, match="Bad Gateway"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_falls_back_to_the_raw_body_when_it_is_not_json(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.rate_limit = tracker.rate_limit.__class__(windows=(), min_interval=0.0, max_retries=0)
        response = _response(status_code=502, text="<html>Bad Gateway</html>")
        response.json.side_effect = ValueError("not json")
        tracker.session.get.return_value = response

        with pytest.raises(TransientError, match="Bad Gateway"):
            tracker.list_activities("2026-08-06", "2026-08-13")


class TestSupportedFormats:
    def test_supports_fit_gpx_and_tcx(self):
        assert PolarTracker.supported_formats == {"FIT", "GPX", "TCX"}


class TestRateLimit:
    """Unlike Wahoo, every Polar request comes from the same v3 API, so every one counts."""

    def _ready(self, tmp_path):
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_listing_spends_the_budget(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body=[SAMPLE_POLAR_EXERCISE])

        tracker.list_activities("2026-08-06", "2026-08-13")

        assert tracker.limiter.requests == 1

    def test_a_download_spends_the_budget_too(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(content=POLAR_FIT_CONTENT)
        activity = Activity(id="2AC312F", name="RUNNING")

        tracker.download(activity, "FIT")

        assert tracker.limiter.requests == 1


class TestInteractiveSetup:
    def test_requires_client_credentials(self, monkeypatch, capsys):
        monkeypatch.delenv("POLAR_CLIENT_ID", raising=False)
        monkeypatch.delenv("POLAR_CLIENT_SECRET", raising=False)

        with pytest.raises(SystemExit):
            PolarTracker.interactive_setup("/app/tokens")

        assert "POLAR_CLIENT_ID" in capsys.readouterr().err

    def test_requires_an_authorization_code(self, monkeypatch, capsys):
        monkeypatch.setenv("POLAR_CLIENT_ID", "abc")
        monkeypatch.setenv("POLAR_CLIENT_SECRET", "shh")
        monkeypatch.setattr("builtins.input", lambda _: "  ")

        with pytest.raises(SystemExit):
            PolarTracker.interactive_setup("/app/tokens")

        assert "authorization code is required" in capsys.readouterr().err

    def test_saves_the_access_token_and_registers_the_user(self, monkeypatch, tmp_path):
        monkeypatch.setenv("POLAR_CLIENT_ID", "abc")
        monkeypatch.setenv("POLAR_CLIENT_SECRET", "shh")
        monkeypatch.setattr("builtins.input", lambda _: "auth-code")

        token_response = _response(json_body=SAMPLE_POLAR_TOKEN_RESPONSE)
        register_response = _response(status_code=200, json_body={"polar-user-id": 2278512})
        mock_post = MagicMock(side_effect=[token_response, register_response])
        monkeypatch.setattr("src.trackers.polar.requests.post", mock_post)

        PolarTracker.interactive_setup(str(tmp_path))

        token_call = mock_post.call_args_list[0]
        assert token_call.args[0] == "https://polarremote.com/v2/oauth2/token"
        assert token_call.kwargs["auth"] == ("abc", "shh")
        assert token_call.kwargs["data"]["code"] == "auth-code"

        register_call = mock_post.call_args_list[1]
        assert register_call.args[0] == USERS_URL
        assert register_call.kwargs["json"]["member-id"]
        assert register_call.kwargs["headers"]["Authorization"] == "Bearer polar-access-token"

        saved = json.loads((tmp_path / "polar" / "tokens.json").read_text())
        assert saved == {"access_token": "polar-access-token"}

    def test_an_already_registered_user_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("POLAR_CLIENT_ID", "abc")
        monkeypatch.setenv("POLAR_CLIENT_SECRET", "shh")
        monkeypatch.setattr("builtins.input", lambda _: "auth-code")

        token_response = _response(json_body=SAMPLE_POLAR_TOKEN_RESPONSE)
        conflict_response = _response(status_code=409, text="already registered")
        mock_post = MagicMock(side_effect=[token_response, conflict_response])
        monkeypatch.setattr("src.trackers.polar.requests.post", mock_post)

        PolarTracker.interactive_setup(str(tmp_path))

        assert json.loads((tmp_path / "polar" / "tokens.json").read_text())["access_token"] == "polar-access-token"

    def test_exits_when_the_token_exchange_fails(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("POLAR_CLIENT_ID", "abc")
        monkeypatch.setenv("POLAR_CLIENT_SECRET", "shh")
        monkeypatch.setattr("builtins.input", lambda _: "bad-code")
        monkeypatch.setattr(
            "src.trackers.polar.requests.post",
            MagicMock(return_value=_response(status_code=400, text="invalid_grant")),
        )

        with pytest.raises(SystemExit):
            PolarTracker.interactive_setup(str(tmp_path))

        assert "token exchange failed" in capsys.readouterr().err
        assert not (tmp_path / "polar" / "tokens.json").exists()

    def test_exits_when_registration_fails_for_a_reason_other_than_conflict(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("POLAR_CLIENT_ID", "abc")
        monkeypatch.setenv("POLAR_CLIENT_SECRET", "shh")
        monkeypatch.setattr("builtins.input", lambda _: "auth-code")

        token_response = _response(json_body=SAMPLE_POLAR_TOKEN_RESPONSE)
        forbidden_response = _response(status_code=403, text="missing consent")
        mock_post = MagicMock(side_effect=[token_response, forbidden_response])
        monkeypatch.setattr("src.trackers.polar.requests.post", mock_post)

        with pytest.raises(SystemExit):
            PolarTracker.interactive_setup(str(tmp_path))

        assert "registration failed" in capsys.readouterr().err
        assert not (tmp_path / "polar" / "tokens.json").exists()
