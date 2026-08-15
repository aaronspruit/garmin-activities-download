# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install deps (runtime + dev)
pip install -r requirements.txt -r requirements-dev.txt

# Run the full test suite with coverage (coverage must stay >= 75%, enforced by pyproject.toml)
pytest --cov

# Run a single test file / test / by keyword
pytest tests/test_trackers_wahoo.py
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

The Dockerfile is multi-stage: `builder` installs [requirements.txt](requirements.txt) into `/opt/venv`, `runtime` (the default target, so the plain `docker build` above) copies that venv in and then deletes `pip`, `ensurepip` and other build-time parts of the base image, and `dev` keeps `pip` for the dev container (`"target": "dev"` in [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json)). Removing `pip` is what keeps the Trivy job green: pip vendors its own dependencies and, since 26.2, ships `pip/_vendor/bom.cdx.json` declaring them, so a scanner reads them as installed packages and flags their CVEs (msgpack, setuptools) even though nothing in `src/` imports them. Anything added to the runtime stage must not reintroduce `pip`.

## Architecture

A **run-once** container: it authenticates each enabled tracker, downloads any new activities, then exits (exit code 0 = success, 1 = auth failure, 3 = unsafe activity ID, 2 = other). This design targets a Kubernetes CronJob or host crontab, not a long-running service. There is no server or scheduler in the code — scheduling lives entirely in the deployment (crontab / [k8s/cronjob.yaml](k8s/cronjob.yaml)).

Garmin is not privileged in the code: it is one implementation of the `Tracker` interface, alongside Wahoo. Everything platform-specific lives under [src/trackers/](src/trackers/); the pipeline around it is tracker-agnostic.

1. **[src/config.py](src/config.py)** — `load_config()` builds a `Config` dataclass from env vars. `TRACKERS` parses via `_parse_trackers()` (validated against the registry, lowercased, deduplicated, order preserved). `DOWNLOAD_TARGETS` parses via `_parse_targets()` into `DownloadTarget(format, folder)`, where **`folder` is the whole destination path relative to `output_dir`** — a destination belongs to a consuming app, so one folder may hold several formats and may be several levels deep. Grammar: `folder=FMT[+FMT]` per entry, with a bare `FMT` short for `{format}=FMT`. `{format}`/`{tracker}` placeholders are rendered at *load* time (both are known there — the tracker from the variable name), so the downloader only ever sees concrete paths. `Config.download_targets` is therefore a `dict[tracker_name, list[DownloadTarget]]`: `<TRACKER>_DOWNLOAD_TARGETS` **replaces** (never merges with) `DOWNLOAD_TARGETS` for that tracker. A format the tracker cannot supply is a startup `ValueError` when named in the tracker's own variable, but only the downloader's runtime warning when merely inherited from the shared default — asserted vs. inferred. An unknown `<PREFIX>_DOWNLOAD_TARGETS` raises (a typo would otherwise silently do nothing); a known but disabled tracker only warns. `DOWNLOAD_FORMATS` is deprecated, parsed by the separate `_parse_legacy_formats()` (its `FMT:subfolder` nests *under* the format, the opposite model), and setting both raises. **`Config` holds no credentials** — each tracker reads its own in `from_env()`, so adding a tracker never touches this dataclass.
2. **[src/trackers/base.py](src/trackers/base.py)** — the `Tracker` ABC (`from_env`, `authenticate`, `list_activities`, `download`, `interactive_setup`), the normalized `Activity(id, name, payload)`, `FORMAT_EXTENSIONS`, and the exceptions (`TrackerAuthError`, `ActivityDownloadError`, `UnsafeActivityIdError`). `download()` returns the **final** file bytes, so unwrapping is the tracker's job and the download loop stays generic. `Activity.payload` carries opaque per-tracker data from listing to download (Wahoo's file URL).
3. **[src/trackers/\_\_init\_\_.py](src/trackers/__init__.py)** — `TRACKER_CLASSES`, built from the `_REGISTERED` tuple. **Adding a tracker = one new module + one entry here.** Imports must stay side-effect-free: CI smoke-tests the image with `python -c "import src.main"`.
4. **[src/downloader.py](src/downloader.py)** — `download_new_activities(tracker, ...)` filters targets to `tracker.supported_formats` (warning about the rest, returning 0 early if none survive), then fetches and writes. Each `activity.id` is checked by `_is_safe_activity_id()` (ASCII alphanumeric) before it reaches a path; failure raises `UnsafeActivityIdError` → exit 3. **Dedup is filesystem-based but keys off a marker, not the file**: an empty file at `<state_dir>/<target.path>/<tracker>-<activityId>.<ext>` (default `<output_dir>/.state/…`) — still no database, no manifest. The indirection exists because consuming apps delete each file once they have imported it, so the file's absence cannot mean "not yet downloaded"; keying off it re-fetched the whole `DAYS_BACK` window every run. An activity file present without its marker is *adopted* (marker written, nothing downloaded) so upgrading an existing install does not re-download. The marker is written **after** the file, so a crash between them is recoverable. Targets are grouped by format so a format wanted in several folders costs one download per activity, written to every folder still missing it. A `download_delay` (default 1s) guards against rate limits.
5. **[src/main.py](src/main.py)** — loops over `config.trackers`. **One tracker's failure must not stop the others**, so each is wrapped independently and the run exits with the most severe code, precedence `3 > 1 > 2 > 0`.

[src/env.py](src/env.py) holds `read_secret()` (`/run/secrets/<name lowercased>` first, then the env var). It is separate from `config.py` purely to break a `config` → `trackers` → `config` import cycle.

### The trackers

- **[src/trackers/garmin.py](src/trackers/garmin.py)** — FIT/GPX/TCX. Saved tokens first, email/password fallback (both optional). `_DL_FORMATS` maps a format token to a `garminconnect` enum plus a zipped flag; FIT is special because Garmin serves it as an `ORIGINAL` zip, so `_extract_fit_bytes()` pulls the first `.fit` member out.
- **[src/trackers/wahoo.py](src/trackers/wahoo.py)** — FIT only. OAuth2 with a hard constraint worth reading the module docstring for: Wahoo revokes the previous access token only after a successful call with its replacement, and caps a user at 10 unrevoked tokens. **So `authenticate()` makes no network call and the refresh happens lazily at the first API call** — a refresh that is never used leaks a token, and ten such runs lock the user out. The rotated refresh token is persisted immediately (temp file + `os.replace`), since the old one is spent the moment the refresh succeeds. `/v1/workouts` has no date filter, so `list_activities` pages through `starts`-descending results until it passes the cutoff. Wahoo also gates the Cloud API on **application approval**, and an unapproved application still authorizes users and still gets valid tokens — the failure surfaces only at the first API call, as a 422 whose *body* names the cause. That is why `_raise_for_error()` replaces `raise_for_status()`: the status line alone sends the operator hunting a malformed query. It maps the not-approved 422 and any 401/403 to `TrackerAuthError` (exit 1, needs a human) and leaves every other status an `HTTPError` (exit 2), all with the body attached.

### Setup vs. main entrypoint

- **[src/main.py](src/main.py)** (`python -m src.main`, the Dockerfile `CMD`) is the headless run — no interactive prompts.
- **[src/setup.py](src/setup.py)** (`python -m src.setup <tracker>`) dispatches to that tracker's `interactive_setup()`. It must be run with `-it` once per tracker before any headless run. This split is the core of the auth model: interactive once, headless forever after. Garmin prompts for credentials and MFA; Wahoo prints an authorize URL and takes the OAuth code back by hand (the container has no browser).

### Output layout (breaking-change awareness)

Files are written as `data/<FORMAT>[/<subfolder>]/<tracker>-<activityId>.<ext>`. The tracker prefix exists because Garmin and Wahoo both hand out plain integer IDs, so an unprefixed name lets them collide and lets dedup wrongly skip a real activity. Because dedup is path-based, files from before the prefix (and, earlier still, GPX written flat into `data/`) are not recognized as already-downloaded — a known breaking change, called out in the release notes rather than the README.

## Configuration

All config is env-var driven (see the table in [README.md](README.md)). Shared vars: `TRACKERS` (default `garmin`), `DAYS_BACK` (default 7), `TOKENS_DIR` (default `/app/tokens`), `OUTPUT_DIR` (default `/app/data`), `STATE_DIR` (default `<OUTPUT_DIR>/.state`, for the dedup markers), `DOWNLOAD_TARGETS` (default `FIT`, accepts `folder=FMT[+FMT]` entries plus bare formats), `<TRACKER>_DOWNLOAD_TARGETS` (per-tracker replacement), and the deprecated `DOWNLOAD_FORMATS`. Per-tracker: `GARMIN_EMAIL`/`GARMIN_PASSWORD` (optional once tokens exist) and `WAHOO_CLIENT_ID`/`WAHOO_CLIENT_SECRET` (**required on every run** — the refresh needs them), plus optional `WAHOO_REDIRECT_URI`. All credentials are secret-aware. Each tracker stores tokens under `<TOKENS_DIR>/<name>`. Docker Compose mounts `./data` and `./tokens` as volumes to persist across the run-once lifecycle.

## Testing notes

No network or real credentials anywhere. [tests/conftest.py](tests/conftest.py) provides `FakeTracker` (a real `Tracker` subclass whose `list_activities`/`download` are `MagicMock`s — deliberately not `MagicMock(spec=Tracker)`, since `name` and `supported_formats` are bare `ClassVar` annotations a spec'd mock would not carry), the `mock_garmin` client mock, and sample GPX/TCX/FIT-zip and Wahoo JSON payloads. Wahoo tests mock at the `requests.Session` level. When changing download logic, update `_DL_FORMATS` and the corresponding sample payloads/zip builders in conftest together.

## CI/CD

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on push/PR to `main` and on `v*.*.*` tags: `lint` + `test` in parallel → `build-push` (publishes to GHCR) → `security-scan` (Trivy, CRITICAL/HIGH) → `release` (only on version tags).

**A tag push never applies version tags or `latest` to the image** — `build-push` sets `flavor: latest=false` and carries no `type=semver` patterns, so the build is reachable only as `sha-<commit>`. `latest` moving at tag time would let a deployment that tracks it pull a release (breaking changes and all) before the notes existed. The version tags are applied afterwards by [.github/workflows/release-image-tags.yml](.github/workflows/release-image-tags.yml), on `release: published`/`released`, with `docker buildx imagetools create` against the *same digest* — no rebuild, so the digest Trivy scanned is the digest that ships and the attestation stays valid. It finds that digest from the `image-digest.txt` asset `release` attaches to the draft, falling back to the `sha-<commit>` tag of the commit the git tag points at. A pre-release gets `X.Y.Z-rc<N>` (first free `N`, probed against the registry) and never `latest`; a full release gets `X.Y.Z`, `X.Y`, `latest`, and `X` above 0.x, but only for a plain `X.Y.Z` tag.

The generated notes are pinned to a **release-to-release** range, not a tag-to-tag one: the `release` job resolves `previous_tag` from `releases/latest` (the last *published*, non-draft, non-prerelease release) and passes it to `softprops/action-gh-release`. Left to itself GitHub can choose an abandoned tag as the base — cut `v0.6.0`, abandon it, cut `v0.7.0`, and everything between `v0.5.2` and `v0.6.0` disappears from the notes while still shipping in the image. The same job deletes an existing *draft* for the tag being built (an abandoned or re-cut version, so the notes regenerate cleanly) and **fails** if a *published* release already owns that tag, since those notes are hand-written and never edited after the fact.

Cut a release by pushing a `vX.Y.Z` tag, then **publish the draft by hand**. The `release` job creates a *draft*, whose body is [.github/release-notes-template.md](.github/release-notes-template.md) followed by GitHub's generated notes (a supplied body is pre-pended to them, not replaced by them). Replace the `{{RELEASE HIGHLIGHTS}}` placeholder in the draft and publish. The draft exists so the highlights are written before anyone is notified — and publishing is now also what moves the image tags. Nothing here should ever be edited after publishing.

The generated half is sorted into sections by [.github/release.yml](.github/release.yml), keyed on the `changelog:` label each PR carries. [.github/workflows/pr-label-validation.yml](.github/workflows/pr-label-validation.yml) fails any PR that does not carry exactly one, since a label forgotten at merge time is only noticed when the release is cut. Dependabot self-labels via [.github/dependabot.yml](.github/dependabot.yml). Use `changelog:skip` for a change that should not appear in the notes at all.
