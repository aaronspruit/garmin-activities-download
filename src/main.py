"""Entry point: authenticate each tracker, download new activities, exit."""

import logging
import sys

from src.config import load_config
from src.downloader import download_new_activities
from src.trackers import TRACKER_CLASSES, TrackerAuthError, UnsafeActivityIdError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Worst-first. A run reports the most serious result of all its trackers, so one
# tracker's authentication failure is not hidden by another's success. Codes 1
# and 3 need an operator; code 2 is often temporary.
_CODE_SEVERITY = [3, 1, 2, 0]


def _worst(codes: list[int]) -> int:
    """Return the most serious of the given exit codes."""
    for code in _CODE_SEVERITY:
        if code in codes:
            return code
    return 0


def _run_tracker(name: str, config) -> tuple[int, int]:
    """Run one tracker. Returns (exit code, files downloaded)."""
    try:
        tracker = TRACKER_CLASSES[name].from_env(config.tokens_dir)
        tracker.authenticate()
        count = download_new_activities(
            tracker,
            output_dir=config.output_dir,
            days_back=config.days_back,
            targets=config.download_targets,
        )
        return 0, count

    except TrackerAuthError as e:
        logger.error("[%s] Authentication failed: %s", name, e)
        return 1, 0

    except UnsafeActivityIdError as e:
        logger.error("[%s] Unsafe activity id in tracker response: %s", name, e)
        return 3, 0

    except Exception as e:
        logger.error("[%s] Download failed: %s", name, e, exc_info=True)
        return 2, 0


def main() -> int:
    """Run the download workflow for every enabled tracker. Returns exit code."""
    try:
        config = load_config()
    except Exception as e:
        logger.error("Invalid configuration: %s", e, exc_info=True)
        return 2

    logger.info(
        "Starting activity download (trackers=%s, days_back=%d, output=%s, targets=%s)",
        ", ".join(config.trackers),
        config.days_back,
        config.output_dir,
        ", ".join(f"{t.format}->{t.path}" for t in config.download_targets),
    )

    # One tracker failing must not stop the others: an expired Wahoo token
    # should never cost a scheduled run its Garmin activities.
    codes = []
    total = 0
    for name in config.trackers:
        code, count = _run_tracker(name, config)
        codes.append(code)
        total += count

    logger.info("Completed: %d new activities downloaded across %d tracker(s)", total, len(config.trackers))
    return _worst(codes)


if __name__ == "__main__":
    sys.exit(main())
