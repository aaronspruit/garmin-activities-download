"""Polar AccessLink API tracker.

Polar uses OAuth2 authorization-code, the same as Wahoo, so the first
authentication needs a browser and `interactive_setup` takes the code back by
hand. AccessLink adds one step Wahoo has no equivalent of: after the code
exchange, the application must register the user with `POST /v3/users` before
any data endpoint works for them. `interactive_setup` does this once, with a
generated `member-id`; a 409 there means the user is already registered, and
that is not an error.

Polar's own reference client (github.com/polarofficial/accesslink-example-
python) never refreshes the access token and stores no expiry -- it uses the
token until the user revokes it in the Polar Flow app. This module does the
same: `authenticate` only loads the saved token, and a token Polar has revoked
surfaces as a 401 from the API, which becomes a `TrackerAuthError` that names
`python -m src.setup polar`. There is no refresh flow to get this wrong.

`GET /v3/exercises` (the AccessLink v3 endpoint this tracker uses) takes no
date filter and returns only what was uploaded to Flow in the last 30 days,
so `list_activities` filters that fixed list to the requested range the same
way Wahoo's unfiltered listing does. A `DAYS_BACK` beyond 30 cannot reach
further back than Polar's own window.

Every exercise file comes from this same v3 API, so this tracker counts every
request -- the listing and every download -- against its rate limit, unlike
Wahoo, which gets its files from a CDN that Wahoo exempts. Polar's TCX export
is gzip-compressed; FIT and GPX are not.

AccessLink v3 (used here) and "AccessLink Dynamic API v4" are two different
APIs, with different OAuth2 endpoints and different data. Only v4 publishes
numeric rate limits, at https://www.polar.com/polar-api-v4/, and only v4's
docs state them: 3000 requests per 15 minutes, 100000 per day. v3's own docs
and swagger (https://www.polar.com/accesslink-api/swagger.yaml) state no
limit of their own, and v4 has no FIT, GPX, or TCX export to replace this
tracker's use of v3. `_RATE_LIMIT` below is therefore a conservative guess,
the same as Garmin's, not the v4 numbers.
"""

import gzip
import json
import logging
import os
import sys
import uuid

import requests

from src.env import read_secret
from src.ratelimit import RateLimitError, RateLimitPolicy, TransientError, Window
from src.trackers.base import Activity, ActivityDownloadError, Tracker, TrackerAuthError

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://flow.polar.com/oauth2/authorization"
TOKEN_URL = "https://polarremote.com/v2/oauth2/token"
API_BASE = "https://www.polaraccesslink.com/v3"
EXERCISES_URL = f"{API_BASE}/exercises"
USERS_URL = f"{API_BASE}/users"

SCOPE = "accesslink.read_all"
# Nothing has to listen at this address: the authorization code is read out of
# the failed page's address bar by hand. Polar's own example client uses a
# plain `http://` redirect for the same reason, unlike Wahoo, which requires
# `https://` even for an address nothing serves.
DEFAULT_REDIRECT_URI = "http://localhost"

_TIMEOUT = 30

# Path suffix of the exercise export endpoint, by format.
_DL_PATHS = {"FIT": "fit", "GPX": "gpx", "TCX": "tcx"}

# Polar publishes no limit for the v3 AccessLink API. See the module
# docstring for why this is a guess and not the v4 numbers.
_RATE_LIMIT = RateLimitPolicy(
    windows=(Window(30, 300), Window(150, 3600), Window(1000, 86400)),
    min_interval=1.0,
    max_retries=3,
    backoff_initial=5.0,
    backoff_max=300.0,
    max_wait=300.0,
    max_downloads=0,
)


def _write_tokens(path: str, tokens: dict) -> None:
    """Write the token file atomically, so a crash cannot truncate it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f, indent=2)
    os.replace(tmp, path)


def _error_message(response: requests.Response) -> str:
    """Pull Polar's own words out of a failed response, when it sends any."""
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("errors") or payload.get("message")
        if message:
            return str(message)

    return str(response.text or "").strip()[:200] or "no response body"


class PolarTracker(Tracker):
    """Polar AccessLink API v3.

    Exposes FIT, GPX, and TCX, one export endpoint for each, for every
    exercise Polar has uploaded to Flow in the last 30 days.
    """

    name = "polar"
    supported_formats = frozenset({"FIT", "GPX", "TCX"})
    rate_limit = _RATE_LIMIT

    def __init__(self, token_path: str) -> None:
        self.token_path = token_path
        self.session = requests.Session()
        self._access_token: str | None = None

    @classmethod
    def _token_path(cls, tokens_dir: str) -> str:
        return os.path.join(tokens_dir, cls.name, "tokens.json")

    @classmethod
    def from_env(cls, tokens_dir: str) -> "PolarTracker":
        return cls(token_path=cls._token_path(tokens_dir))

    def authenticate(self) -> None:
        """Load the saved access token. Makes no network call.

        There is no refresh to do here -- see the module docstring for why.
        """
        try:
            with open(self.token_path) as f:
                tokens = json.load(f)
        except FileNotFoundError:
            raise TrackerAuthError(
                f"No Polar tokens at {self.token_path}. Run `python -m src.setup polar` to create them."
            ) from None
        except (OSError, ValueError) as e:
            raise TrackerAuthError(f"Polar tokens at {self.token_path} could not be read: {e}") from e

        access_token = tokens.get("access_token")
        if not access_token:
            raise TrackerAuthError(
                f"Polar tokens at {self.token_path} have no access_token. Run `python -m src.setup polar` again."
            )

        self._access_token = access_token
        logger.info("Loaded saved Polar access token")

    def _headers(self) -> dict:
        if self._access_token is None:
            raise TrackerAuthError("Polar tracker used before authenticate() succeeded")
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get(self, url: str, **kwargs) -> requests.Response:
        """Make one counted API request, with pacing and retries.

        Every Polar request in this tracker -- the listing and every download
        alike -- comes from the same v3 API and counts against the same
        guessed budget, unlike Wahoo's CDN-hosted, exempt downloads.
        """
        return self.limiter.call(self._get_once, url, **kwargs)

    def _get_once(self, url: str, **kwargs) -> requests.Response:
        response = self.session.get(url, headers=self._headers(), timeout=_TIMEOUT, **kwargs)
        self._raise_for_error(response)
        return response

    @staticmethod
    def _raise_for_error(response: requests.Response) -> None:
        if response.status_code < 400:
            return

        message = _error_message(response)

        if response.status_code == 429:
            raise RateLimitError(f"Polar refused the request because of its rate limit: {message}")

        if response.status_code >= 500:
            raise TransientError(f"The Polar API failed ({response.status_code}): {message}")

        if response.status_code == 404:
            # Only an exercise export URL carries an id, so this is a missing
            # file, not a missing listing.
            raise ActivityDownloadError(f"Polar exercise not found ({response.status_code}): {message}")

        if response.status_code == 403:
            raise TrackerAuthError(
                f"Polar refused the request ({response.status_code}): {message}. "
                "This usually means the user has not accepted every mandatory consent "
                "in the Polar Flow app."
            )

        if response.status_code == 401:
            raise TrackerAuthError(
                f"Polar rejected the access token ({response.status_code}): {message}. "
                "Run `python -m src.setup polar` to authorize again."
            )

        raise requests.HTTPError(
            f"Polar API request to {response.url} failed ({response.status_code}): {message}",
            response=response,
        )

    def list_activities(self, start_date: str, end_date: str) -> list[Activity]:
        """List exercises in the date range. See the module docstring for the 30-day window."""
        response = self._get(EXERCISES_URL)
        exercises = response.json()

        activities = []
        for exercise in exercises:
            # Local start time, no offset, so comparing the date prefix as
            # text orders correctly, the same as Wahoo's `starts`.
            exercise_date = str(exercise.get("start_time", ""))[:10]
            if not (start_date <= exercise_date <= end_date):
                continue

            exercise_id = exercise.get("id")
            if not exercise_id:
                logger.debug("Skipping a Polar exercise with no id")
                continue

            # The exercise schema has no free-text name, unlike Garmin and
            # Wahoo -- only a sport, and sometimes a more specific one.
            name = exercise.get("detailed_sport_info") or exercise.get("sport") or "Unknown"
            activities.append(Activity(id=str(exercise_id), name=name))

        return activities

    def download(self, activity: Activity, fmt: str) -> bytes:
        """Fetch one exercise file. TCX is gzip-compressed; FIT and GPX are not."""
        url = f"{EXERCISES_URL}/{activity.id}/{_DL_PATHS[fmt]}"
        response = self._get(url)
        return gzip.decompress(response.content) if fmt == "TCX" else response.content

    @classmethod
    def interactive_setup(cls, tokens_dir: str) -> None:
        """Walk the operator through the OAuth2 exchange, then register the user."""
        client_id = read_secret("POLAR_CLIENT_ID")
        client_secret = read_secret("POLAR_CLIENT_SECRET")
        redirect_uri = os.environ.get("POLAR_REDIRECT_URI", DEFAULT_REDIRECT_URI)

        if not client_id or not client_secret:
            print(
                "Error: POLAR_CLIENT_ID and POLAR_CLIENT_SECRET are required.\n"
                "Create an API client at https://admin.polaraccesslink.com first.",
                file=sys.stderr,
            )
            sys.exit(1)

        authorize_url = (
            f"{AUTHORIZE_URL}?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&scope={SCOPE}"
        )

        print("Polar AccessLink Authentication Setup")
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

        print("\nExchanging the code for an access token...")
        response = requests.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            headers={"Accept": "application/json;charset=UTF-8"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            timeout=_TIMEOUT,
        )
        if response.status_code >= 400:
            print(
                f"Error: The token exchange failed ({response.status_code}): {response.text}",
                file=sys.stderr,
            )
            sys.exit(1)

        access_token = response.json()["access_token"]

        print("Registering the user with this application...")
        register_response = requests.post(
            USERS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"member-id": uuid.uuid4().hex},
            timeout=_TIMEOUT,
        )
        if register_response.status_code == 409:
            print("This user is already registered with this application. Continuing.")
        elif register_response.status_code >= 400:
            print(
                f"Error: User registration failed ({register_response.status_code}): "
                f"{_error_message(register_response)}. If this names a missing consent, accept "
                "every mandatory consent in the Polar Flow app first, then run this setup again.",
                file=sys.stderr,
            )
            sys.exit(1)

        token_path = cls._token_path(tokens_dir)
        _write_tokens(token_path, {"access_token": access_token})

        print(f"\nTokens saved to {token_path}")
        print("You can now run the container in headless mode.")
