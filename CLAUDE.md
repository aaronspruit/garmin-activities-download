# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install deps (runtime + dev)
pip install -r requirements.txt -r requirements-dev.txt

# Run the full test suite with coverage (coverage must stay >= 75%, enforced by pyproject.toml)
pytest --cov

# Run a single test file / test / by keyword
pytest tests/test_downloader.py
pytest tests/test_downloader.py::test_name
pytest -k "dedup"

# Lint and format-check (CI runs both; must pass)
ruff check .
ruff format --check .
ruff format .            # apply formatting

# Build the image locally
docker build -t garmin-activities-download:test .
```

Runs against Python 3.14 in CI (matching the Dockerfile `FROM python:3.14-slim`); project requires >= 3.12. Ruff line length is 120.

## Architecture

A **run-once** container: it authenticates, downloads any new Garmin Connect activities, then exits (exit code 0 = success, 1 = auth failure, 3 = unsafe activity ID from Garmin, 2 = other). This design targets a Kubernetes CronJob or host crontab, not a long-running service. There is no server or scheduler in the code — scheduling lives entirely in the deployment (crontab / [k8s/cronjob.yaml](k8s/cronjob.yaml)).

The workflow is a straight three-stage pipeline wired in [src/main.py](src/main.py):

1. **[src/config.py](src/config.py)** — `load_config()` builds a `Config` dataclass from env vars, with Docker-secret fallback: `_read_secret()` reads `/run/secrets/<name lowercased>` first, then the env var. `DOWNLOAD_FORMATS` parses into a list of `DownloadTarget(format, folder)` — each comma-separated entry is `FORMAT` (folder defaults to the format name) or `FORMAT:folder`. Formats are validated against `{FIT, GPX, TCX}`; folders must be a single safe path component. Both raise `ValueError`. A repeated `(format, folder)` pair is silently deduplicated, keeping first-occurrence order.
2. **[src/auth.py](src/auth.py)** — `authenticate()` tries saved tokens (`Garmin.login(tokenstore)`) first; only on failure does it fall back to email/password login (which then persists fresh tokens). With neither, it raises `AuthenticationError`. Headless runs rely on tokens already existing — see setup below.
3. **[src/downloader.py](src/downloader.py)** — `download_new_activities()` fetches activities in the `days_back` window and downloads each configured target. Each `activityId` from the Garmin response is checked by `_is_safe_activity_id()` (ASCII alphanumeric only) before it reaches a path; a failure raises `UnsafeActivityIdError`, which `main.py` maps to exit code 3. **Dedup is filesystem-based**: an activity/target is skipped if `<output_dir>/<folder>/<activityId>.<ext>` already exists — there is no database or manifest. Behavior per format is driven by the `FORMAT_SPECS` table. Targets are grouped by format so a format wanted in several folders costs one Garmin download per activity, written to every folder still missing it. FIT is special: Garmin returns it as an `ORIGINAL`-format zip, so `_extract_fit_bytes()` pulls the first `.fit` member out before writing. A `download_delay` (default 1s) between downloads guards against rate limits.

### Setup vs. main entrypoint

- **[src/main.py](src/main.py)** (`python -m src.main`, the Dockerfile `CMD`) is the headless run — no interactive prompts.
- **[src/setup.py](src/setup.py)** (`python -m src.setup`) is a separate **interactive** one-time script for initial auth including MFA (`prompt_mfa`). It must be run with `-it` before any headless run so tokens exist in the token store. This split is the core of the auth model: interactive once, headless forever after.

### Output layout (breaking-change awareness)

Files are written into one subfolder per target, named after the format unless overridden: `data/FIT/`, `data/GPX/`, `data/TCX/`. Earlier versions wrote GPX flat into `data/`. Because dedup is path-based, flat legacy files are not recognized as already-downloaded — this is a known breaking change noted in the README.

## Configuration

All config is env-var driven (see the table in [README.md](README.md)). Key vars: `GARMIN_EMAIL`, `GARMIN_PASSWORD` (both secret-aware), `DAYS_BACK` (default 7), `GARMINTOKENS` (default `/app/tokens`), `OUTPUT_DIR` (default `/app/data`), `DOWNLOAD_FORMATS` (default `FIT`, accepts `FORMAT[:folder]` entries and repeated formats). Docker Compose mounts `./data` and `./tokens` as volumes to persist across the run-once lifecycle.

## Testing notes

Tests mock the `garminconnect.Garmin` client entirely — see the `mock_garmin` fixture and sample GPX/TCX/FIT-zip payloads in [tests/conftest.py](tests/conftest.py). No network or real Garmin credentials are involved. When changing download logic, update `FORMAT_SPECS` and the corresponding sample payloads/zip builders in conftest together.

## CI/CD

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on push/PR to `main` and on `v*.*.*` tags: `lint` + `test` in parallel → `build-push` (publishes to GHCR) → `security-scan` (Trivy, CRITICAL/HIGH) → `release` (only on version tags). Cut a release by pushing a `vX.Y.Z` tag.
