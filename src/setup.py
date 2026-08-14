"""One-time interactive setup for tracker authentication.

Run this script interactively to authenticate a tracker and save persistent
tokens. After setup, the container can run headless.

Usage:
    docker compose run --rm -it garmin-sync python -m src.setup garmin
    docker compose run --rm -it garmin-sync python -m src.setup wahoo
    kubectl run tracker-setup --rm -it --image=<image> -- python -m src.setup garmin
"""

import os
import sys

from src.trackers import TRACKER_CLASSES


def setup(argv: list[str] | None = None) -> None:
    """Dispatch interactive setup to the tracker named on the command line."""
    args = sys.argv[1:] if argv is None else argv
    available = ", ".join(sorted(TRACKER_CLASSES))

    if len(args) != 1:
        print(f"Usage: python -m src.setup <tracker>\nAvailable trackers: {available}", file=sys.stderr)
        sys.exit(1)

    name = args[0].strip().lower()
    if name not in TRACKER_CLASSES:
        print(f"Error: Unknown tracker {name!r}.\nAvailable trackers: {available}", file=sys.stderr)
        sys.exit(1)

    tokens_dir = os.environ.get("TOKENS_DIR", "/app/tokens")
    TRACKER_CLASSES[name].interactive_setup(tokens_dir)


if __name__ == "__main__":
    setup()
