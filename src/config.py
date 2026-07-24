"""Configuration loading from environment variables and Docker secrets."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Application configuration."""

    email: str | None
    password: str | None
    tokenstore: str
    output_dir: str
    days_back: int


def _read_secret(name: str) -> str | None:
    """Read a value from Docker secret file, falling back to env var."""
    secret_path = f"/run/secrets/{name.lower()}"
    try:
        with open(secret_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get(name)


def load_config() -> Config:
    """Load configuration from environment variables and Docker secrets."""
    return Config(
        email=_read_secret("GARMIN_EMAIL"),
        password=_read_secret("GARMIN_PASSWORD"),
        tokenstore=os.environ.get("GARMINTOKENS", "/app/tokens"),
        output_dir=os.environ.get("OUTPUT_DIR", "/app/data"),
        days_back=int(os.environ.get("DAYS_BACK", "7")),
    )
