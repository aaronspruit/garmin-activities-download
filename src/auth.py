"""Authentication and token management for Garmin Connect."""

import logging

from garminconnect import Garmin, GarminConnectAuthenticationError

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails and no fallback is available."""


def authenticate(
    email: str | None = None,
    password: str | None = None,
    tokenstore: str = "/app/tokens",
) -> Garmin:
    """Authenticate with token persistence and credential fallback.

    Tries loading saved tokens first. If that fails, attempts a fresh
    login with email/password. Raises AuthenticationError if neither works.
    """
    garmin = Garmin()
    try:
        garmin.login(tokenstore)
        logger.info("Authenticated using saved tokens")
        return garmin
    except (FileNotFoundError, GarminConnectAuthenticationError, Exception) as e:
        logger.info("Saved token login failed: %s", e)

    if not email or not password:
        raise AuthenticationError(
            "No valid tokens and no credentials provided. Run the setup script to generate tokens."
        )

    garmin = Garmin(email=email, password=password)
    garmin.login(tokenstore)
    logger.info("Authenticated with credentials, tokens saved")
    return garmin
