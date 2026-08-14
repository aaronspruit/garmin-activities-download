"""Environment and Docker-secret reading.

Kept separate from `src.config` so tracker modules can read their own secrets
without a `config` -> `trackers` -> `config` import cycle.
"""

import os


def read_secret(name: str) -> str | None:
    """Read a value from a Docker secret file, falling back to the env var."""
    secret_path = f"/run/secrets/{name.lower()}"
    try:
        with open(secret_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get(name)
