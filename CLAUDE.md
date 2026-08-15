# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when it works in this repository.

## Mandatory rules

These four rules are not optional. They apply to every change.

1. **Write all documentation with `/simple-english`.** This covers the README, this file, comments and docstrings, commit messages, pull request titles and bodies, release notes, and messages the operator reads. Invoke the `simple-english` skill first, then write the text. Do not write the text first and clean it up later.
2. **Breaking-change detail belongs only in the pull request.** Do not put migration steps, upgrade instructions, or "this breaks X" in the README or in this file. Put them in the pull request body, and give the pull request the correct `changelog:` label. The label carries the detail into the release notes. The README and this file describe how the code works now. The release notes are the only record of what changed. A design note can say why the current behavior exists, but it must not tell a user what to do about an older install.
3. **Comments describe the present, not the past.** A comment says what the current config does and why. It does not say what the config replaced, or what an older version did. `git log` and `git blame` hold that, and a stale "X still serves Y" line is wrong the moment Y changes. When you migrate something, write the new file as if it was always that way, and strip the same kind of history out of every file the change touches. The migration story goes in the commit message and the pull request.
4. **Write the fewest sentences that carry the fact.** State each fact in one place, and link to that place from anywhere else that needs it. Do not repeat what a linked file already says. Do not write a preamble, a list of what comes next, or a closing restatement. If a section changes nothing about what the operator does, delete the section instead of shortening it.

## Commands

```bash
# Install deps (runtime + dev)
pip install -r requirements.txt -r requirements-dev.txt

# Run the full test suite with coverage (coverage must stay >= 75%, set in pyproject.toml)
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

CI runs Python 3.14, which matches the `python:3.14-slim` base image. The project needs 3.12 or later. The ruff line length is 120.

[Dockerfile](Dockerfile) has four stages. `base` holds the shared user and workdir. `builder` installs [requirements.txt](requirements.txt) into `/opt/venv`. `dev` keeps `pip` for the dev container, which [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json) selects with `"target": "dev"`. `runtime` is last, so a plain `docker build` produces it.

**Nothing added to the `runtime` stage can reintroduce `pip`.** That stage deletes `pip`, `ensurepip`, and other build-time parts of the base image, which is what keeps the Trivy job green. `pip` vendors its own dependencies and, since version 26.2, ships `pip/_vendor/bom.cdx.json` to declare them. A scanner reads that file as an installed package list and reports their CVEs, even though nothing in [src/](src/) imports them.

## Architecture

The container runs once. It authenticates each enabled tracker, downloads new activities, then exits. Exit code 0 means success, 1 an auth failure, 3 an unsafe activity ID, and 2 anything else. The target is a Kubernetes CronJob or a host crontab, not a long-running service. There is no scheduler in the code. Scheduling lives in the deployment, in the crontab or in [k8s/cronjob.yaml](k8s/cronjob.yaml).

Garmin holds no special position in the code. It is one implementation of the `Tracker` interface, next to Wahoo. All platform-specific code stays under [src/trackers/](src/trackers/). The rest of the pipeline names no tracker.

**[src/config.py](src/config.py)** — `load_config()` builds the `Config` dataclass from environment variables.

- `TRACKERS` goes through `_parse_trackers()`. Names are validated against the registry, lowercased, and deduplicated, and keep first-occurrence order.
- `DOWNLOAD_TARGETS` goes through `_parse_targets()` and produces `DownloadTarget(format, folder)`. **`folder` is the whole destination path under `output_dir`.** A destination belongs to a consuming application, so one folder can hold several formats and can be several levels deep.
- The grammar is `folder=FMT[+FMT]` for each entry. A bare `FMT` is short for `{format}=FMT`.
- `{format}` and `{tracker}` resolve at load time, because both are known there. The tracker comes from the variable name. The downloader therefore sees concrete paths only.
- `Config.download_targets` is a `dict[tracker_name, list[DownloadTarget]]`. `<TRACKER>_DOWNLOAD_TARGETS` **replaces** `DOWNLOAD_TARGETS` for that tracker. It never merges with it.
- A format the tracker cannot supply raises a startup `ValueError` when the tracker's own variable names it. The same format only produces a runtime warning from the downloader when the tracker inherits it from the shared default. The first case is asserted, the second is inferred.
- An unknown `<PREFIX>_DOWNLOAD_TARGETS` raises, because a typo otherwise does nothing at all. A known but disabled tracker only warns.
- `_validate_folder()` and `_assert_within()` keep every destination inside `output_dir`.
- `DOWNLOAD_FORMATS` is deprecated and has its own parser, `_parse_legacy_formats()`. Its `FMT:subfolder` nests under the format, which is the opposite model. Setting both variables raises.
- **`Config` holds no credentials.** Each tracker reads its own in `from_env()`, so a new tracker never changes this dataclass.

**[src/trackers/base.py](src/trackers/base.py)** — the `Tracker` ABC (`from_env`, `authenticate`, `list_activities`, `download`, `interactive_setup`), the normalized `Activity(id, name, payload)`, `FORMAT_EXTENSIONS`, and the exceptions `TrackerAuthError`, `ActivityDownloadError`, and `UnsafeActivityIdError`. `download()` returns the **final** file bytes, so each tracker unwraps its own archives and the download loop stays generic. `Activity.payload` carries opaque per-tracker data from listing to download, such as the file URL from Wahoo. It also holds the `rate_limit` class attribute and the lazy `limiter` property.

**[src/ratelimit.py](src/ratelimit.py)** — `RateLimitPolicy`, `RateLimiter`, `load_policy`, and the exceptions `RateLimitError`, `TransientError`, and `BudgetExhaustedError`.

- **A tracker declares its limits once, as `rate_limit`, and gets everything else.** Pacing, retries, backoff, the per-run cap, and every `<TRACKER>_` environment variable follow from that one attribute. A new tracker needs no other change. State in a comment whether the numbers are published or a guess.
- `RateLimiter.call()` counts a request against the windows. `RateLimiter.retry()` retries without counting, for a request the platform exempts. **Only the tracker knows which of its requests count**, so the choice belongs there and nowhere else.
- Windows are sliding counters, so a burst at the start of a run cannot exceed a published limit.
- **`BudgetExhaustedError` is not a failure.** A wait longer than `max_wait` raises it, the run stops early, and the exit code stays `0`. The markers make it safe: the next run continues at the same activity. Waiting out a daily limit would hold the container open for hours and overlap the next schedule.
- `load_policy` reads the environment directly, for the same reason as `env.py`: a loader inside `config.py` would make a `config` → `trackers` → `config` cycle. `load_config` still calls it once for each tracker, so a typo stops the run at startup rather than at the first request.
- `RateLimiter` resolves `time.sleep` and `time.monotonic` at each call, not at construction, so a test can replace them after a limiter exists. `tests/conftest.py` makes every wait instant with an autouse fixture.
- [README.md](README.md) holds the variable names, the defaults, and the limits of each tracker. Do not repeat them here.

**[src/trackers/\_\_init\_\_.py](src/trackers/__init__.py)** — `TRACKER_CLASSES`, built from the `_REGISTERED` tuple. **A new tracker is one new module plus one entry here.** Every import must stay free of side effects, because CI smoke-tests the image with `python -c "import src.main"`.

**[src/downloader.py](src/downloader.py)** — `download_new_activities(tracker, ...)` first drops targets the tracker cannot supply. It warns about them and returns 0 when none survive. Then it fetches and writes.

- `_is_safe_activity_id()` checks each `activity.id` for ASCII letters and digits before it reaches a path. A failure raises `UnsafeActivityIdError`, which becomes exit code 3.
- **Dedup reads a marker, not the activity file.** The marker is an empty file at `<state_dir>/<target.path>/<tracker>-<activityId>.<ext>`, under `<output_dir>/.state/` by default. There is still no database and no manifest.
- The marker exists because consuming applications delete each activity file after import. The absence of the file therefore cannot mean "not yet downloaded". Keying off the file re-fetched the whole `DAYS_BACK` window on every run.
- An activity file present without its marker is *adopted*. The run writes the marker and downloads nothing.
- **The marker is written after the file**, so a crash between the two is recoverable.
- Targets are grouped by format. One format wanted in several folders costs one download for each activity, written to every folder that is still missing it.
- **The downloader holds no delay of its own.** Pacing belongs to the tracker, which knows its own limits. For a rate limit, the loop catches `BudgetExhaustedError`, records what it wrote, and reports how many activities it did not reach. It still catches `ActivityDownloadError` for one activity that cannot be fetched.
- `max_downloads` comes from the tracker policy and caps one run. It is checked between activities, so a run can pass it by the number of formats minus one, and all formats of one activity stay together. `0` leaves the windows alone to limit the run, which is the usual case.

**[src/main.py](src/main.py)** — loops over `config.trackers`. **One tracker's failure must not stop the others.** Each tracker is wrapped on its own, and the run exits with the most serious code. The precedence is `3 > 1 > 2 > 0`. `BudgetExhaustedError` is caught before the other handlers and gives `0`, because a rate limit defers work and never loses it. A new exit code would make a Kubernetes CronJob report a normal backfill as a failed pod.

[src/env.py](src/env.py) holds `read_secret()`, which reads `/run/secrets/<name lowercased>` first and then the environment variable. It is separate from `config.py` only to break a `config` → `trackers` → `config` import cycle.

### The trackers

**[src/trackers/garmin.py](src/trackers/garmin.py)** — FIT, GPX, and TCX. It tries saved tokens first and falls back to email and password. Both credentials are optional. `_DL_FORMATS` maps a format token to a `garminconnect` enum and a zipped flag. FIT is the special case: Garmin serves it as an `ORIGINAL` zip, so `_extract_fit_bytes()` pulls out the first `.fit` member.

**Garmin publishes no rate limit.** The Connect Developer Program pages state none, and this tracker uses the undocumented consumer web API. `_RATE_LIMIT` is therefore a guess, and it is slow on purpose: a Garmin 429 applies to the account, survives a change of address, and has locked users out for 24 to 48 hours. Everything counts here, the login included. `garminconnect` retries the 5xx and network failures itself and always fails fast on a 429, so `_counted()` only translates the 429 and lets the library keep the rest. **A 429 during `authenticate()` must not fall through to the credential login.** The broad `except Exception` that drives that fallback is for an expired token. A second login after a refusal only makes the lockout longer.

**[src/trackers/wahoo.py](src/trackers/wahoo.py)** — FIT only, over OAuth2. Read the module docstring before you change this file. Two constraints shape it:

- Wahoo revokes the previous access token only after a successful call with its replacement. It also allows 10 unrevoked tokens for each user. **So `authenticate()` makes no network call, and the refresh happens lazily at the first API call.** A refresh that nothing uses leaks a token, and ten such runs lock the user out. The rotated refresh token is persisted at once, with a temp file and `os.replace`. The old one is spent as soon as the refresh succeeds.
- Wahoo also limits the Cloud API to approved applications. An unapproved application still authorizes users and still receives valid tokens. The failure therefore appears only at the first API call, as a 422 whose *body* names the cause. `_raise_for_error()` replaces `raise_for_status()` for that reason: the status line alone sends the operator hunting a malformed query. It maps the not-approved 422 and any 401 or 403 to `TrackerAuthError`, which is exit code 1 and needs a human. Every other status stays an `HTTPError`, which is exit code 2. All of them carry the response body.

`/v1/workouts` has no date filter, so `list_activities` pages through `starts`-descending results until it passes the cutoff.

Wahoo publishes its limits, in two tiers. `_RATE_LIMITS` holds both, and `WAHOO_APP_TIER` picks one. The tier belongs to the application registration: the operator asks Wahoo for a sandbox or a production application, and an approved application stays in the tier it was asked for. **The tier is therefore a fact about the registration, not a state that approval changes.** Sandbox is the default, because this container serves one person.

Wahoo exempts the authentication, the token refresh, and the file downloads, so `_get()` is counted and `download()` uses `limiter.retry()` instead. Every response also carries `X-RateLimit-Remaining` and `X-RateLimit-Reset`, which `_sync_limits()` reads. **The headers are the truth and the local counters are only an estimate**, because they miss the requests of an earlier run and of any other client of the same application.

**`list_activities()` keeps the pages it already read when a limit stops it.** Only the list spends budget, so a partial list still downloads every activity it found. Discarding the pages would spend the whole budget of a run and write nothing, and the next run would start at page 1 and stop in the same place.

### Setup and the main entrypoint

- **[src/main.py](src/main.py)** (`python -m src.main`, the Dockerfile `CMD`) is the headless run. It has no prompts.
- **[src/setup.py](src/setup.py)** (`python -m src.setup <tracker>`) dispatches to that tracker's `interactive_setup()`. Run it once for each tracker, with `-it`, before any headless run. This split is the core of the auth model: interactive once, headless afterwards. Garmin asks for credentials and an MFA code. Wahoo prints an authorize URL and takes the OAuth code back by hand, because the container has no browser.

### Output layout

Files are written to `data/<FORMAT>[/<subfolder>]/<tracker>-<activityId>.<ext>`. The tracker prefix exists because Garmin and Wahoo both hand out plain integer IDs. Without the prefix their files collide, and dedup then skips a real activity. Dedup is path-based, so any change to this layout changes what counts as already downloaded. Such a change is a breaking change: describe it in the pull request, under rule 2 above, and not here.

## Configuration

Environment variables drive all configuration. [README.md](README.md) holds the full table.

- Shared: `TRACKERS` (default `garmin`), `DAYS_BACK` (default 7), `TOKENS_DIR` (default `/app/tokens`), `OUTPUT_DIR` (default `/app/data`), `STATE_DIR` (default `<OUTPUT_DIR>/.state`, for the dedup markers), `DOWNLOAD_TARGETS` (default `FIT`), `<TRACKER>_DOWNLOAD_TARGETS`, and the deprecated `DOWNLOAD_FORMATS`.
- Rate limits: `load_policy()` in [src/ratelimit.py](src/ratelimit.py) holds the whole list. Note that the per-tracker form of `RATE_LIMIT_MAX_WAIT` is `<TRACKER>_MAX_WAIT`, which is the one name that does not simply take a prefix.
- Garmin: `GARMIN_EMAIL` and `GARMIN_PASSWORD`, both optional once tokens exist.
- Wahoo: `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET`, **required on every run** because the refresh needs them, plus the optional `WAHOO_REDIRECT_URI` and `WAHOO_APP_TIER` (default `sandbox`).

Every credential can come from a Docker secret. Each tracker stores its tokens under `<TOKENS_DIR>/<name>`. Docker Compose mounts `./data` and `./tokens` as volumes, so both survive the run-once lifecycle.

## Testing notes

The tests use no network and no real credentials. [tests/conftest.py](tests/conftest.py) provides:

- `FakeTracker`, a real `Tracker` subclass whose `list_activities` and `download` are `MagicMock`s. It is deliberately not a `MagicMock(spec=Tracker)`, because `name` and `supported_formats` are bare `ClassVar` annotations that a spec'd mock does not carry.
- `mock_garmin`, a `garminconnect` client mock.
- Sample GPX, TCX, and FIT-zip payloads, and sample Wahoo JSON.

- `fake_time`, an autouse fixture that replaces the whole `src.ratelimit.time` reference with a clock that moves only when something sleeps. **It must replace `sleep` and `monotonic` together.** A no-op `sleep` beside a real clock leaves the time unchanged after a wait, so the windows never bind, the limiter admits more requests than a published limit allows, and no test can see it. Without the fixture the Wahoo retry tests take 30 seconds each.
- `clean_rate_limit_env`, an autouse fixture that hides the rate limit variables of the real environment. The shared names (`MAX_RETRIES`, `BACKOFF_MAX`) are generic enough to sit in a developer shell already. It lists the exact names that `load_policy` reads, because a match on the suffix alone would also delete `DATABASE_MAX_WAIT` and its like.

Wahoo tests mock at the `requests.Session` level. When you change download logic, update `_DL_FORMATS` and the matching sample payloads and zip builders in conftest together. Give every mocked response a real `headers` dict: a bare `MagicMock` answers `headers.get()` with another mock, and the rate limit headers then read as something they are not.

## CI/CD

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on push and pull request to `main`, and on `v*.*.*` tags. The order is `lint` and `test` in parallel, then `build-push` (publishes to GHCR), then `security-scan` (Trivy, CRITICAL and HIGH, fixed vulnerabilities only), then `release` on version tags only.

### Image tags

**A tag push applies no version tags and no `latest`.** `build-push` sets `flavor: latest=false` and carries no `type=semver` pattern, so the build is reachable only as `sha-<commit>`. `latest` must not move before the notes exist, or a deployment that tracks it pulls a release that nobody can read about yet.

[.github/workflows/release-image-tags.yml](.github/workflows/release-image-tags.yml) applies the version tags afterwards, on `release: published` and `released`. It uses `docker buildx imagetools create` against the **same digest**, so there is no rebuild. The digest that Trivy scanned is the digest that ships, and the attestation stays valid. The workflow reads that digest from the `image-digest.txt` asset that `release` attaches to the draft. If the asset is gone, it falls back to the `sha-<commit>` tag of the commit that the git tag points at.

A pre-release gets `X.Y.Z-rc<N>` and never `latest`. `N` is the first free number, probed against the registry. A full release gets `X.Y.Z`, `X.Y`, and `latest`, plus `X` above 0.x, but only for a plain `X.Y.Z` tag.

### Releases

Cut a release by pushing a `vX.Y.Z` tag. Then **publish the draft by hand.** The `release` job creates a *draft* whose body is [.github/release-notes-template.md](.github/release-notes-template.md) followed by the notes GitHub generates. A supplied body is pre-pended to the generated notes, not replaced by them. Replace the `{{RELEASE HIGHLIGHTS}}` placeholder in the draft, then publish.

The draft exists so that somebody writes the highlights before anybody is notified. Publishing is also what moves the image tags. Never edit a release after you publish it.

The generated notes cover a **release-to-release** range, not a tag-to-tag one. The `release` job resolves `previous_tag` from `releases/latest` and passes it to `softprops/action-gh-release`. That endpoint returns the last published release, which is neither a draft nor a pre-release. Left alone, GitHub can pick an abandoned tag as the base. Cut `v0.6.0`, abandon it, cut `v0.7.0`, and everything between `v0.5.2` and `v0.6.0` disappears from the notes while it still ships in the image.

The same job deletes an existing *draft* for the tag it is building, which is an abandoned or re-cut version, so the notes regenerate cleanly. It **fails** when a *published* release already owns that tag, because those notes are written by hand and are never edited afterwards.

### Labels

[.github/release.yml](.github/release.yml) sorts the generated notes into sections, keyed on the `changelog:` label that each pull request carries. [.github/workflows/pr-label-validation.yml](.github/workflows/pr-label-validation.yml) fails any pull request that does not carry exactly one. A label forgotten at merge time is noticed only when the release is cut. Dependabot labels itself through [.github/dependabot.yml](.github/dependabot.yml). Use `changelog:skip` for a change that must not appear in the notes at all.
