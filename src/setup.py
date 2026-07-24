"""One-time interactive setup for Garmin Connect authentication.

Run this script interactively to authenticate (including MFA if enabled)
and generate persistent tokens. After setup, the container can run headless.

Usage:
    docker compose run --rm -it garmin-sync python -m src.setup
    kubectl run garmin-setup --rm -it --image=<image> -- python -m src.setup
"""

import os
import sys

from garminconnect import Garmin


def setup() -> None:
    """Interactive authentication setup with MFA support."""
    tokenstore = os.environ.get("GARMINTOKENS", "/app/tokens")

    print("Garmin Connect Authentication Setup")
    print("=" * 40)
    email = input("Email: ").strip()
    password = input("Password: ").strip()

    if not email or not password:
        print("Error: Email and password are required.", file=sys.stderr)
        sys.exit(1)

    print("\nAuthenticating...")
    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("MFA code: ").strip(),
    )
    garmin.login(tokenstore)

    print(f"\nTokens saved to {tokenstore}")
    print("You can now run the container in headless mode.")


if __name__ == "__main__":
    setup()
