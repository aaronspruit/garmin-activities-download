"""Entry point: authenticate, download new activities, exit."""

import logging
import sys

from src.auth import AuthenticationError, authenticate
from src.config import load_config
from src.downloader import UnsafeActivityIdError, download_new_activities

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> int:
    """Run the download workflow. Returns exit code."""
    try:
        config = load_config()
        logger.info(
            "Starting Garmin activity download (days_back=%d, output=%s, targets=%s)",
            config.days_back,
            config.output_dir,
            ", ".join(f"{t.format}->{t.path}" for t in config.download_targets),
        )

        garmin = authenticate(
            email=config.email,
            password=config.password,
            tokenstore=config.tokenstore,
        )

        count = download_new_activities(
            garmin,
            output_dir=config.output_dir,
            days_back=config.days_back,
            targets=config.download_targets,
        )

        logger.info("Completed: %d new activities downloaded", count)
        return 0

    except AuthenticationError as e:
        logger.error("Authentication failed: %s", e)
        return 1

    except UnsafeActivityIdError as e:
        logger.error("Unsafe activity id in Garmin response: %s", e)
        return 3

    except Exception as e:
        logger.error("Download failed: %s", e, exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
