"""Wahoo Cloud API tracker.

Wahoo uses OAuth2 authorization-code, not a username and password, so the first
authentication needs a browser. `interactive_setup` prints an authorization URL,
takes the code back by hand, and saves the tokens. Headless runs then refresh.

Token lifecycle, from the Wahoo documentation:

* An access token lasts 2 hours. A refresh returns a new refresh token too.
* Refreshing alone does not revoke the previous access token. Wahoo revokes it
  only after a successful API call with the new token.
* Since 1 January 2026, an application may hold 10 unrevoked access tokens for
  each user.

Together these mean a refresh that is not followed by an API call leaks a token,
and ten such runs lock the user out. Therefore this module refreshes lazily, at
the first API call, and never in `authenticate`. To recover from a lockout, send
`DELETE https://api.wahooligan.com/v1/permissions` and run the setup again.
"""

import json
import logging
import os
import sys
import time

import requests

from src.env import read_secret
from src.trackers.base import Activity, ActivityDownloadError, Tracker, TrackerAuthError

logger = logging.getLogger(__name__)

API_BASE = "https://api.wahooligan.com"
AUTHORIZE_URL = f"{API_BASE}/oauth/authorize"
TOKEN_URL = f"{API_BASE}/oauth/token"
WORKOUTS_URL = f"{API_BASE}/v1/workouts"

SCOPES = "user_read workouts_read offline_data"
# https, because Wahoo's developer portal expects it -- an `http://` redirect URI
# is not usable there, localhost included. Nothing has to listen at this address:
# the authorization code is read out of the failed page's address bar by hand. It
# only has to match the URI registered for the application, exactly.
DEFAULT_REDIRECT_URI = "https://localhost"

# Refresh this many seconds before the access token actually expires, so a slow
# run cannot have its token expire mid-flight.
_EXPIRY_MARGIN = 300

_PER_PAGE = 30
# Backstop against an unexpected response that never satisfies the date cutoff.
_MAX_PAGES = 100
_TIMEOUT = 30

# Wahoo limits the Cloud API to applications it has approved, and says so in the
# body of a 422. An unapproved application still authorizes users and still gets
# working tokens, so the failure only appears here, on the first real call --
# where a bare "422 Unprocessable Content" reads like a malformed query and
# sends the operator looking at the wrong thing entirely.
_NOT_APPROVED = "has not been approved"


def _write_tokens(path: str, tokens: dict) -> None:
    """Write the token file atomically, so a crash cannot truncate it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f, indent=2)
    os.replace(tmp, path)


def _error_message(response: requests.Response) -> str:
    """Pull Wahoo's own words out of a failed response.

    Wahoo answers errors with `{"error": "..."}`, which `raise_for_status()`
    discards -- it reports only the status line.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("errors") or payload.get("message")
        if message:
            return str(message)

    return str(response.text or "").strip()[:200] or "no response body"


def _token_response_to_tokens(payload: dict) -> dict:
    """Normalize an /oauth/token response into what we persist."""
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + float(payload.get("expires_in", 7200)),
    }


class WahooTracker(Tracker):
    """Wahoo Cloud API.

    Only FIT is available: Wahoo exposes one activity file for each workout, and
    it is always a FIT file.
    """

    name = "wahoo"
    supported_formats = frozenset({"FIT"})

    def __init__(self, token_path: str, client_id: str, client_secret: str) -> None:
        self.token_path = token_path
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = requests.Session()
        self._tokens: dict | None = None

    @classmethod
    def _token_path(cls, tokens_dir: str) -> str:
        return os.path.join(tokens_dir, cls.name, "tokens.json")

    @classmethod
    def from_env(cls, tokens_dir: str) -> "WahooTracker":
        client_id = read_secret("WAHOO_CLIENT_ID")
        client_secret = read_secret("WAHOO_CLIENT_SECRET")
        # Unlike Garmin's optional email and password, these are needed on every
        # run: refreshing the access token will not work without them.
        if not client_id or not client_secret:
            raise TrackerAuthError(
                "WAHOO_CLIENT_ID and WAHOO_CLIENT_SECRET are required for every run. "
                "Register an application at the Wahoo developer portal to get them."
            )
        return cls(
            token_path=cls._token_path(tokens_dir),
            client_id=client_id,
            client_secret=client_secret,
        )

    def authenticate(self) -> None:
        """Load the saved tokens. Makes no network call by design.

        Refreshing here would mint an access token that a run with nothing to do
        never uses, and Wahoo would leave it unrevoked. See the module docstring.
        """
        try:
            with open(self.token_path) as f:
                self._tokens = json.load(f)
        except FileNotFoundError:
            raise TrackerAuthError(
                f"No Wahoo tokens at {self.token_path}. Run `python -m src.setup wahoo` to create them."
            ) from None
        except (OSError, ValueError) as e:
            raise TrackerAuthError(f"Wahoo tokens at {self.token_path} could not be read: {e}") from e

        if not self._tokens.get("refresh_token"):
            raise TrackerAuthError(
                f"Wahoo tokens at {self.token_path} have no refresh_token. Run `python -m src.setup wahoo` again."
            )
        logger.info("Loaded saved Wahoo tokens")

    def _access_token(self) -> str:
        """Return a usable access token, refreshing only when necessary.

        Called immediately before each API call, never ahead of time.
        """
        if self._tokens is None:
            raise TrackerAuthError("Wahoo tracker used before authenticate() succeeded")

        access_token = self._tokens.get("access_token")
        if access_token and time.time() < self._tokens.get("expires_at", 0) - _EXPIRY_MARGIN:
            return access_token

        logger.info("Wahoo access token expired or missing, refreshing")
        response = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._tokens["refresh_token"],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=_TIMEOUT,
        )
        if response.status_code >= 400:
            raise TrackerAuthError(
                f"Wahoo token refresh failed ({response.status_code}): {_error_message(response)}. "
                f"Run `python -m src.setup wahoo` to authorize again."
            )

        # Wahoo rotates the refresh token, so the old one is now spent. Persist
        # the new pair before using it -- losing it means re-running setup.
        self._tokens = _token_response_to_tokens(response.json())
        _write_tokens(self.token_path, self._tokens)
        logger.info("Wahoo tokens refreshed and saved")
        return self._tokens["access_token"]

    def _get(self, url: str, **kwargs) -> requests.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        return self.session.get(url, headers=headers, timeout=_TIMEOUT, **kwargs)

    @staticmethod
    def _raise_for_error(response: requests.Response) -> None:
        """Turn a failed API response into an exception that names the cause.

        Two of these end the run for this tracker rather than being reported as
        a generic failure: an application Wahoo has not approved, and a token
        the API rejects. Both need an operator, and neither is fixed by waiting
        for the next scheduled run.
        """
        if response.status_code < 400:
            return

        message = _error_message(response)

        if _NOT_APPROVED in message:
            raise TrackerAuthError(
                f"Wahoo refused the request ({response.status_code}): {message}. "
                "The tokens are fine -- it is the application that lacks Cloud API access. "
                "Request it at https://developer.wahoo.com/applications. Running "
                "`python -m src.setup wahoo` again will not help and leaves another unrevoked "
                "access token against the 10-token limit."
            )

        if response.status_code in (401, 403):
            raise TrackerAuthError(
                f"Wahoo rejected the access token ({response.status_code}): {message}. "
                "Run `python -m src.setup wahoo` to authorize again."
            )

        raise requests.HTTPError(
            f"Wahoo API request to {response.url} failed ({response.status_code}): {message}",
            response=response,
        )

    def list_activities(self, start_date: str, end_date: str) -> list[Activity]:
        """List workouts in the date range.

        Wahoo has no date filter. It returns workouts sorted by `starts`,
        newest first, so we page until we pass the start of the range.
        """
        activities: list[Activity] = []

        for page in range(1, _MAX_PAGES + 1):
            response = self._get(WORKOUTS_URL, params={"page": page, "per_page": _PER_PAGE})
            self._raise_for_error(response)
            workouts = response.json().get("workouts", [])
            if not workouts:
                break

            reached_start = False
            for workout in workouts:
                # ISO 8601, so comparing the date prefix as text orders correctly.
                workout_date = str(workout.get("starts", ""))[:10]
                if workout_date < start_date:
                    reached_start = True
                    break
                if workout_date > end_date:
                    continue

                workout_id = workout.get("id")
                file_url = (workout.get("workout_summary") or {}).get("file", {}).get("url")
                if not file_url:
                    logger.debug("Skipping Wahoo workout %s: no activity file", workout_id)
                    continue

                activities.append(
                    Activity(
                        id=str(workout_id),
                        name=workout.get("name") or "Unknown",
                        payload={"file_url": file_url},
                    )
                )

            if reached_start or len(workouts) < _PER_PAGE:
                break
        else:
            logger.warning("Stopped listing Wahoo workouts after %d pages", _MAX_PAGES)

        return activities

    def download(self, activity: Activity, fmt: str) -> bytes:
        """Fetch the workout's FIT file from the URL found while listing."""
        file_url = activity.payload.get("file_url")
        if not file_url:
            raise ActivityDownloadError(f"Wahoo workout {activity.id} has no activity file URL")

        # The file lives on a CDN and the URL carries its own credentials, so
        # this request must not use the bearer token.
        response = self.session.get(file_url, timeout=_TIMEOUT)
        if response.status_code >= 400:
            raise ActivityDownloadError(
                f"Wahoo file download for workout {activity.id} failed ({response.status_code})"
            )
        return response.content

    @classmethod
    def interactive_setup(cls, tokens_dir: str) -> None:
        """Walk the operator through the OAuth2 authorization-code exchange."""
        client_id = read_secret("WAHOO_CLIENT_ID")
        client_secret = read_secret("WAHOO_CLIENT_SECRET")
        redirect_uri = os.environ.get("WAHOO_REDIRECT_URI", DEFAULT_REDIRECT_URI)

        if not client_id or not client_secret:
            print(
                "Error: WAHOO_CLIENT_ID and WAHOO_CLIENT_SECRET are required.\n"
                "Register an application at the Wahoo developer portal first.",
                file=sys.stderr,
            )
            sys.exit(1)

        authorize_url = (
            f"{AUTHORIZE_URL}?client_id={client_id}&redirect_uri={redirect_uri}"
            f"&scope={SCOPES.replace(' ', '+')}&response_type=code"
        )

        print("Wahoo Cloud API Authentication Setup")
        print("=" * 40)
        print("1. Open this URL in a browser and approve access:\n")
        print(f"   {authorize_url}\n")
        print(f"2. Your browser is then sent to {redirect_uri}. Nothing listens there,")
        print("   so the page fails to load. This is expected.")
        print("3. Copy the `code` value out of the address bar.\n")

        code = input("Authorization code: ").strip()
        if not code:
            print("Error: An authorization code is required.", file=sys.stderr)
            sys.exit(1)

        print("\nExchanging the code for tokens...")
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            timeout=_TIMEOUT,
        )
        if response.status_code >= 400:
            print(
                f"Error: The token exchange failed ({response.status_code}): {response.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        token_path = cls._token_path(tokens_dir)
        _write_tokens(token_path, _token_response_to_tokens(response.json()))

        print(f"\nTokens saved to {token_path}")
        print("You can now run the container in headless mode.")
        # Valid tokens are not enough on their own, and nothing here can tell
        # whether the application is approved -- only an API call reveals that,
        # and making one now would spend a token to learn it.
        print(
            "\nIf a run then fails with 422 and 'This application has not been approved',\n"
            "the tokens are fine and the application is not: Wahoo limits the Cloud API to\n"
            "approved applications. Request approval at https://developer.wahoo.com/applications\n"
            "and wait for it. Running this setup again cannot fix it, and each run holds one\n"
            "more of your 10 access tokens."
        )
