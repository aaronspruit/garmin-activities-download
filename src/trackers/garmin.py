"""Garmin Connect tracker."""

import io
import logging
import os
import sys
import zipfile

from garminconnect import Garmin, GarminConnectTooManyRequestsError

from src.env import read_secret
from src.ratelimit import RateLimitError, RateLimitPolicy, Window
from src.trackers.base import Activity, ActivityDownloadError, Tracker, TrackerAuthError

logger = logging.getLogger(__name__)

# Download format and whether Garmin serves it inside a zip. FIT has no format
# of its own: Garmin returns it as an ORIGINAL-format archive.
_DL_FORMATS = {
    "FIT": (Garmin.ActivityDownloadFormat.ORIGINAL, True),
    "GPX": (Garmin.ActivityDownloadFormat.GPX, False),
    "TCX": (Garmin.ActivityDownloadFormat.TCX, False),
}

# Garmin publishes no limit for this API. The Connect Developer Program pages
# state none, and this tracker uses the consumer web API, which has no
# documentation at all. These numbers are therefore a conservative guess, not a
# published fact.
#
# The guess is slow on purpose. A Garmin 429 applies to the account, not to the
# address or the client, so a new address does not recover it, and it has locked
# users out for 24 to 48 hours. A run that takes an extra hour costs far less
# than an account that no run can reach for two days. Raise the numbers with
# `GARMIN_RATE_LIMIT` once your own account shows that it accepts more.
_RATE_LIMIT = RateLimitPolicy(
    windows=(Window(20, 60), Window(300, 3600), Window(2000, 86400)),
    min_interval=2.0,
    # A 429 here is a lockout, not a moment of load, so a retry rarely helps and
    # can deepen it. Two patient retries, then the run stops and reports.
    max_retries=2,
    backoff_initial=30.0,
    backoff_max=300.0,
    max_wait=300.0,
    max_downloads=0,
)


def _counted(func, *args, **kwargs):
    """Run one `garminconnect` call and name a rate limit refusal.

    The library retries the 5xx and network failures itself and always fails
    fast on a 429, which is the division this module needs. The limiter adds the
    pacing and the retries for the 429 alone.
    """
    try:
        return func(*args, **kwargs)
    except GarminConnectTooManyRequestsError as e:
        raise RateLimitError(f"Garmin refused the request because of its rate limit: {e}") from e


def _extract_fit_bytes(zip_bytes: bytes) -> bytes:
    """Extract the first .fit member's bytes from an ORIGINAL-format zip archive."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        fit_names = [name for name in zf.namelist() if name.lower().endswith(".fit")]
        if not fit_names:
            raise ActivityDownloadError("ORIGINAL download zip contains no .fit file")
        return zf.read(fit_names[0])


class GarminTracker(Tracker):
    """Garmin Connect, via the `garminconnect` library.

    Authenticates from saved tokens, falling back to email and password. The
    credentials are optional: a token store seeded by `interactive_setup` is
    enough for headless runs.
    """

    name = "garmin"
    supported_formats = frozenset(_DL_FORMATS)
    rate_limit = _RATE_LIMIT

    def __init__(self, tokenstore: str, email: str | None = None, password: str | None = None) -> None:
        self.tokenstore = tokenstore
        self.email = email
        self.password = password
        self.client: Garmin | None = None

    @classmethod
    def _tokenstore(cls, tokens_dir: str) -> str:
        return os.path.join(tokens_dir, cls.name)

    @classmethod
    def from_env(cls, tokens_dir: str) -> "GarminTracker":
        return cls(
            tokenstore=cls._tokenstore(tokens_dir),
            email=read_secret("GARMIN_EMAIL"),
            password=read_secret("GARMIN_PASSWORD"),
        )

    def authenticate(self) -> None:
        """Authenticate with token persistence and credential fallback.

        Tries loading saved tokens first. If that fails, attempts a fresh login
        with email and password, which then persists new tokens.

        A login counts against the rate limit, because Garmin refuses these
        endpoints first and hardest. A refusal stops the tracker here. The
        credential fallback exists for a token that expired, and a second login
        after a 429 only makes the lockout longer.
        """
        client = Garmin()
        try:
            self.limiter.call(_counted, client.login, self.tokenstore)
            logger.info("Authenticated using saved tokens")
            self.client = client
            return
        except RateLimitError as e:
            raise TrackerAuthError(
                f"Garmin refused the login because of its rate limit: {e}. "
                f"This limit applies to the account and can last many hours. "
                f"Wait for the next scheduled run. Do not log in again in the meantime."
            ) from e
        except Exception as e:
            logger.info("Saved token login failed: %s", e)

        if not self.email or not self.password:
            raise TrackerAuthError(
                "No valid tokens and no credentials provided. Run `python -m src.setup garmin` to generate tokens."
            )

        client = Garmin(email=self.email, password=self.password)
        self.limiter.call(_counted, client.login, self.tokenstore)
        logger.info("Authenticated with credentials, tokens saved")
        self.client = client

    def _require_client(self) -> Garmin:
        if self.client is None:
            raise TrackerAuthError("Garmin tracker used before authenticate() succeeded")
        return self.client

    def list_activities(self, start_date: str, end_date: str) -> list[Activity]:
        client = self._require_client()
        activities = self.limiter.call(_counted, client.get_activities_by_date, start_date, end_date)
        return [Activity(id=str(a["activityId"]), name=a.get("activityName", "Unknown")) for a in activities]

    def download(self, activity: Activity, fmt: str) -> bytes:
        # Garmin counts every download, unlike Wahoo, which serves its files
        # from a CDN that its limits exempt.
        dl_fmt, zipped = _DL_FORMATS[fmt]
        client = self._require_client()
        data = self.limiter.call(_counted, client.download_activity, activity.id, dl_fmt=dl_fmt)
        return _extract_fit_bytes(data) if zipped else data

    @classmethod
    def interactive_setup(cls, tokens_dir: str) -> None:
        """Prompt for credentials and MFA, then save tokens."""
        tokenstore = cls._tokenstore(tokens_dir)

        print("Garmin Connect Authentication Setup")
        print("=" * 40)
        email = input("Email: ").strip()
        password = input("Password: ").strip()

        if not email or not password:
            print("Error: Email and password are required.", file=sys.stderr)
            sys.exit(1)

        print("\nAuthenticating...")
        client = Garmin(
            email=email,
            password=password,
            prompt_mfa=lambda: input("MFA code: ").strip(),
        )
        client.login(tokenstore)

        print(f"\nTokens saved to {tokenstore}")
        print("You can now run the container in headless mode.")
