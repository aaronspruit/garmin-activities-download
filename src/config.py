"""Configuration loading from environment variables and Docker secrets."""

import os
from dataclasses import dataclass

VALID_DOWNLOAD_FORMATS = {"FIT", "GPX", "TCX"}


@dataclass
class Config:
    """Application configuration."""

    email: str | None
    password: str | None
    tokenstore: str
    output_dir: str
    days_back: int
    download_formats: list[str]


def _read_secret(name: str) -> str | None:
    """Read a value from Docker secret file, falling back to env var."""
    secret_path = f"/run/secrets/{name.lower()}"
    try:
        with open(secret_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get(name)


def _parse_formats(raw: str) -> list[str]:
    """Parse and validate a comma-separated DOWNLOAD_FORMATS value."""
    formats = [f.strip().upper() for f in raw.split(",") if f.strip()]
    if not formats:
        raise ValueError("DOWNLOAD_FORMATS must not be empty")
    invalid = sorted(set(formats) - VALID_DOWNLOAD_FORMATS)
    if invalid:
        raise ValueError(
            f"Invalid DOWNLOAD_FORMATS value(s): {invalid}. Valid options: {sorted(VALID_DOWNLOAD_FORMATS)}"
        )
    return formats


def load_config() -> Config:
    """Load configuration from environment variables and Docker secrets."""
    return Config(
        email=_read_secret("GARMIN_EMAIL"),
        password=_read_secret("GARMIN_PASSWORD"),
        tokenstore=os.environ.get("GARMINTOKENS", "/app/tokens"),
        output_dir=os.environ.get("OUTPUT_DIR", "/app/data"),
        days_back=int(os.environ.get("DAYS_BACK", "7")),
        download_formats=_parse_formats(os.environ.get("DOWNLOAD_FORMATS", "FIT")),
    )
