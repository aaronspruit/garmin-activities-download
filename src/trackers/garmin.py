"""Garmin Connect tracker."""

import io
import logging
import os
import sys
import zipfile

from garminconnect import Garmin

from src.env import read_secret
from src.trackers.base import Activity, ActivityDownloadError, Tracker, TrackerAuthError

logger = logging.getLogger(__name__)

# Download format and whether Garmin serves it inside a zip. FIT has no format
# of its own: Garmin returns it as an ORIGINAL-format archive.
_DL_FORMATS = {
    "FIT": (Garmin.ActivityDownloadFormat.ORIGINAL, True),
    "GPX": (Garmin.ActivityDownloadFormat.GPX, False),
    "TCX": (Garmin.ActivityDownloadFormat.TCX, False),
}


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
        """
        client = Garmin()
        try:
            client.login(self.tokenstore)
            logger.info("Authenticated using saved tokens")
            self.client = client
            return
        except Exception as e:
            logger.info("Saved token login failed: %s", e)

        if not self.email or not self.password:
            raise TrackerAuthError(
                "No valid tokens and no credentials provided. Run `python -m src.setup garmin` to generate tokens."
            )

        client = Garmin(email=self.email, password=self.password)
        client.login(self.tokenstore)
        logger.info("Authenticated with credentials, tokens saved")
        self.client = client

    def _require_client(self) -> Garmin:
        if self.client is None:
            raise TrackerAuthError("Garmin tracker used before authenticate() succeeded")
        return self.client

    def list_activities(self, start_date: str, end_date: str) -> list[Activity]:
        activities = self._require_client().get_activities_by_date(start_date, end_date)
        return [Activity(id=str(a["activityId"]), name=a.get("activityName", "Unknown")) for a in activities]

    def download(self, activity: Activity, fmt: str) -> bytes:
        dl_fmt, zipped = _DL_FORMATS[fmt]
        data = self._require_client().download_activity(activity.id, dl_fmt=dl_fmt)
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
