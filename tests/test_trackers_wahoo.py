"""Tests for the Wahoo Cloud API tracker.

The token rules get the most attention here. Wahoo revokes a previous access
token only after a successful call with its replacement, and caps a user at 10
unrevoked tokens, so a refresh that is never used leaks one. These tests pin the
behaviour that keeps a scheduled run alive indefinitely.
"""

import json
import time
from unittest.mock import MagicMock

import pytest
import requests

from src.trackers.base import Activity, ActivityDownloadError, TrackerAuthError
from src.trackers.wahoo import WahooTracker
from tests.conftest import (
    SAMPLE_WAHOO_TOKEN_RESPONSE,
    SAMPLE_WAHOO_TOKENS,
    SAMPLE_WAHOO_WORKOUT,
    SAMPLE_WAHOO_WORKOUT_NO_FILE,
    WAHOO_FIT_CONTENT,
)


def _response(status_code=200, json_body=None, content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.content = content
    return response


def _write_tokens(tmp_path, tokens=None):
    path = tmp_path / "wahoo" / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens if tokens is not None else SAMPLE_WAHOO_TOKENS))
    return path


def _tracker(tmp_path):
    tracker = WahooTracker(
        token_path=str(tmp_path / "wahoo" / "tokens.json"),
        client_id="client-id",
        client_secret="client-secret",
    )
    tracker.session = MagicMock()
    return tracker


class TestFromEnv:
    def test_reads_credentials_and_token_path(self, monkeypatch):
        monkeypatch.setenv("WAHOO_CLIENT_ID", "abc")
        monkeypatch.setenv("WAHOO_CLIENT_SECRET", "shh")

        tracker = WahooTracker.from_env("/app/tokens")

        assert tracker.client_id == "abc"
        assert tracker.client_secret == "shh"
        assert tracker.token_path == "/app/tokens/wahoo/tokens.json"

    @pytest.mark.parametrize("missing", ["WAHOO_CLIENT_ID", "WAHOO_CLIENT_SECRET"])
    def test_requires_client_credentials(self, monkeypatch, missing):
        """Unlike Garmin's optional email, these are needed on every run to refresh."""
        monkeypatch.setenv("WAHOO_CLIENT_ID", "abc")
        monkeypatch.setenv("WAHOO_CLIENT_SECRET", "shh")
        monkeypatch.delenv(missing)

        with pytest.raises(TrackerAuthError, match="required for every run"):
            WahooTracker.from_env("/app/tokens")


class TestAuthenticate:
    def test_makes_no_network_call(self, tmp_path):
        """Refreshing here would mint a token a no-op run never uses, leaking it."""
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)

        tracker.authenticate()

        tracker.session.post.assert_not_called()
        tracker.session.get.assert_not_called()

    def test_raises_when_token_file_is_missing(self, tmp_path):
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="python -m src.setup wahoo"):
            tracker.authenticate()

    def test_raises_when_token_file_is_corrupt(self, tmp_path):
        path = tmp_path / "wahoo" / "tokens.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="could not be read"):
            tracker.authenticate()

    def test_raises_when_refresh_token_is_absent(self, tmp_path):
        _write_tokens(tmp_path, {"access_token": "a", "expires_at": 0})
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="no refresh_token"):
            tracker.authenticate()

    def test_raises_when_used_before_authenticating(self, tmp_path):
        tracker = _tracker(tmp_path)

        with pytest.raises(TrackerAuthError, match="before authenticate"):
            tracker.list_activities("2026-08-06", "2026-08-13")


class TestTokenRefresh:
    """A daily run always finds the 2-hour access token expired, so this is the normal path."""

    def test_refreshes_on_first_api_call_and_persists_rotated_tokens(self, tmp_path):
        token_path = _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.session.post.return_value = _response(json_body=SAMPLE_WAHOO_TOKEN_RESPONSE)
        tracker.session.get.return_value = _response(json_body={"workouts": []})
        tracker.authenticate()

        tracker.list_activities("2026-08-06", "2026-08-13")

        sent = tracker.session.post.call_args.kwargs["data"]
        assert sent["grant_type"] == "refresh_token"
        assert sent["refresh_token"] == "saved-refresh-token"
        assert sent["client_id"] == "client-id"

        # Wahoo spends the old refresh token, so the new pair must be on disk.
        saved = json.loads(token_path.read_text())
        assert saved["refresh_token"] == "rotated-refresh-token"
        assert saved["access_token"] == "rotated-access-token"
        assert saved["expires_at"] > time.time()

    def test_uses_the_new_access_token_for_the_api_call(self, tmp_path):
        """Wahoo revokes the previous token only once a call succeeds with this one."""
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.session.post.return_value = _response(json_body=SAMPLE_WAHOO_TOKEN_RESPONSE)
        tracker.session.get.return_value = _response(json_body={"workouts": []})
        tracker.authenticate()

        tracker.list_activities("2026-08-06", "2026-08-13")

        headers = tracker.session.get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer rotated-access-token"

    def test_reuses_a_still_valid_access_token(self, tmp_path):
        """Stops a frequent cron burning a token on every run."""
        _write_tokens(
            tmp_path,
            {"access_token": "still-good", "refresh_token": "r", "expires_at": time.time() + 3600},
        )
        tracker = _tracker(tmp_path)
        tracker.session.get.return_value = _response(json_body={"workouts": []})
        tracker.authenticate()

        tracker.list_activities("2026-08-06", "2026-08-13")

        tracker.session.post.assert_not_called()
        assert tracker.session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer still-good"

    def test_refreshes_when_inside_the_expiry_margin(self, tmp_path):
        """A token expiring mid-run is no good, so refresh a little early."""
        _write_tokens(
            tmp_path,
            {"access_token": "nearly-done", "refresh_token": "r", "expires_at": time.time() + 60},
        )
        tracker = _tracker(tmp_path)
        tracker.session.post.return_value = _response(json_body=SAMPLE_WAHOO_TOKEN_RESPONSE)
        tracker.session.get.return_value = _response(json_body={"workouts": []})
        tracker.authenticate()

        tracker.list_activities("2026-08-06", "2026-08-13")

        tracker.session.post.assert_called_once()

    def test_raises_when_refresh_is_rejected(self, tmp_path):
        _write_tokens(tmp_path)
        tracker = _tracker(tmp_path)
        tracker.session.post.return_value = _response(status_code=401)
        tracker.authenticate()

        with pytest.raises(TrackerAuthError, match="refresh failed"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_token_file_survives_a_failed_write_target(self, tmp_path):
        """The write is atomic, so the old tokens are never half-overwritten."""
        token_path = _write_tokens(tmp_path)
        original = token_path.read_text()
        tracker = _tracker(tmp_path)
        tracker.session.post.return_value = _response(status_code=500)
        tracker.authenticate()

        with pytest.raises(TrackerAuthError):
            tracker.list_activities("2026-08-06", "2026-08-13")

        assert token_path.read_text() == original


class TestListActivities:
    def _ready(self, tmp_path):
        _write_tokens(
            tmp_path,
            {"access_token": "good", "refresh_token": "r", "expires_at": time.time() + 3600},
        )
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_normalizes_workouts_and_keeps_the_file_url(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body={"workouts": [SAMPLE_WAHOO_WORKOUT]})

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [(a.id, a.name) for a in activities] == [("4471", "Morning Ride")]
        # The URL only appears in the listing, so it rides along to download().
        assert activities[0].payload["file_url"] == "https://cdn.wahooligan.com/workouts/4471.fit"

    def test_skips_workouts_with_no_activity_file(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(
            json_body={"workouts": [SAMPLE_WAHOO_WORKOUT, SAMPLE_WAHOO_WORKOUT_NO_FILE]}
        )

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [a.id for a in activities] == ["4471"]

    def test_stops_paging_at_the_start_of_the_date_range(self, tmp_path):
        """Wahoo has no date filter, so paging must stop once it passes the cutoff."""
        tracker = self._ready(tmp_path)
        in_range = {**SAMPLE_WAHOO_WORKOUT, "id": 1, "starts": "2026-08-12T06:00:00.000Z"}
        too_old = {**SAMPLE_WAHOO_WORKOUT, "id": 2, "starts": "2026-08-01T06:00:00.000Z"}
        tracker.session.get.return_value = _response(json_body={"workouts": [in_range, too_old]})

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [a.id for a in activities] == ["1"]
        assert tracker.session.get.call_count == 1

    def test_skips_workouts_newer_than_the_range_without_stopping(self, tmp_path):
        tracker = self._ready(tmp_path)
        too_new = {**SAMPLE_WAHOO_WORKOUT, "id": 9, "starts": "2026-08-20T06:00:00.000Z"}
        in_range = {**SAMPLE_WAHOO_WORKOUT, "id": 1, "starts": "2026-08-12T06:00:00.000Z"}
        tracker.session.get.return_value = _response(json_body={"workouts": [too_new, in_range]})

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert [a.id for a in activities] == ["1"]

    def test_pages_until_a_short_page(self, tmp_path):
        tracker = self._ready(tmp_path)
        full_page = [{**SAMPLE_WAHOO_WORKOUT, "id": n, "starts": "2026-08-12T06:00:00.000Z"} for n in range(30)]
        tracker.session.get.side_effect = [
            _response(json_body={"workouts": full_page}),
            _response(json_body={"workouts": [{**SAMPLE_WAHOO_WORKOUT, "id": 99}]}),
        ]

        activities = tracker.list_activities("2026-08-06", "2026-08-13")

        assert len(activities) == 31
        assert tracker.session.get.call_count == 2
        assert tracker.session.get.call_args.kwargs["params"] == {"page": 2, "per_page": 30}

    def test_stops_on_an_empty_page(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(json_body={"workouts": []})

        assert tracker.list_activities("2026-08-06", "2026-08-13") == []
        assert tracker.session.get.call_count == 1


class TestApiErrors:
    """A failed call must say what Wahoo said, not just which status it returned."""

    def _ready(self, tmp_path):
        _write_tokens(
            tmp_path,
            {"access_token": "good", "refresh_token": "r", "expires_at": time.time() + 3600},
        )
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_an_unapproved_application_is_an_auth_error_not_a_bad_request(self, tmp_path):
        """Wahoo's 422 for this reads like a malformed query, and setup cannot fix it."""
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(
            status_code=422,
            json_body={"error": "This application has not been approved by Wahoo Fitness at this time"},
        )

        with pytest.raises(TrackerAuthError, match="has not been approved") as excinfo:
            tracker.list_activities("2026-08-06", "2026-08-13")

        assert "developer.wahoo.com/applications" in str(excinfo.value)

    def test_a_rejected_token_is_an_auth_error(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=401, json_body={"error": "unauthorized"})

        with pytest.raises(TrackerAuthError, match="unauthorized"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_other_failures_keep_the_http_error_but_carry_the_body(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=422, json_body={"error": "per_page is invalid"})

        with pytest.raises(requests.HTTPError, match="per_page is invalid"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_falls_back_to_the_raw_body_when_it_is_not_json(self, tmp_path):
        tracker = self._ready(tmp_path)
        response = _response(status_code=502)
        response.json.side_effect = ValueError("not json")
        response.text = "<html>Bad Gateway</html>"
        tracker.session.get.return_value = response

        with pytest.raises(requests.HTTPError, match="Bad Gateway"):
            tracker.list_activities("2026-08-06", "2026-08-13")

    def test_reports_an_empty_body_rather_than_nothing(self, tmp_path):
        tracker = self._ready(tmp_path)
        response = _response(status_code=500)
        response.text = ""
        tracker.session.get.return_value = response

        with pytest.raises(requests.HTTPError, match="no response body"):
            tracker.list_activities("2026-08-06", "2026-08-13")


class TestDownload:
    def _ready(self, tmp_path):
        _write_tokens(
            tmp_path,
            {"access_token": "good", "refresh_token": "r", "expires_at": time.time() + 3600},
        )
        tracker = _tracker(tmp_path)
        tracker.authenticate()
        return tracker

    def test_fetches_the_file_url_found_while_listing(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(content=WAHOO_FIT_CONTENT)
        activity = Activity(id="4471", name="Morning Ride", payload={"file_url": "https://cdn/4471.fit"})

        data = tracker.download(activity, "FIT")

        assert data == WAHOO_FIT_CONTENT
        assert tracker.session.get.call_args.args[0] == "https://cdn/4471.fit"

    def test_does_not_send_the_bearer_token_to_the_cdn(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(content=WAHOO_FIT_CONTENT)
        activity = Activity(id="4471", name="Morning Ride", payload={"file_url": "https://cdn/4471.fit"})

        tracker.download(activity, "FIT")

        assert "headers" not in tracker.session.get.call_args.kwargs

    def test_raises_when_the_activity_has_no_file_url(self, tmp_path):
        tracker = self._ready(tmp_path)

        with pytest.raises(ActivityDownloadError, match="no activity file URL"):
            tracker.download(Activity(id="4471", name="Morning Ride"), "FIT")

    def test_raises_when_the_cdn_rejects_the_request(self, tmp_path):
        tracker = self._ready(tmp_path)
        tracker.session.get.return_value = _response(status_code=403)
        activity = Activity(id="4471", name="Morning Ride", payload={"file_url": "https://cdn/4471.fit"})

        with pytest.raises(ActivityDownloadError, match="failed \\(403\\)"):
            tracker.download(activity, "FIT")


class TestSupportedFormats:
    def test_supports_fit_only(self):
        """Wahoo exposes one activity file per workout, and it is always FIT."""
        assert WahooTracker.supported_formats == {"FIT"}
