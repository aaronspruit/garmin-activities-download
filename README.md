
# Garmin Activities Download

This container downloads GPS activities as FIT, GPX, or TCX files. It supports more than one tracker: Garmin Connect and Wahoo. It runs one time, then it exits. You can deploy it with Docker Compose or with a Kubernetes CronJob.

## Overview

The container connects to each configured tracker and downloads the recent activities. It saves each activity as a FIT, GPX, or TCX file. It authenticates with saved tokens, downloads the new activities, then exits. This run-once design fits a Kubernetes CronJob or a host crontab entry. The container is not a long-running service.

The `TRACKERS` variable selects the trackers. Each tracker keeps its own tokens and reads its own credentials. If one tracker fails, the other trackers continue. For the list of trackers, read [Trackers](#trackers).

The authentication tokens stay on disk between runs. Each tracker needs an interactive setup one time. Each scheduled run after that uses the saved tokens with no user input. The container refreshes the tokens and saves them again.

The container writes a marker file for each activity file that it downloads. It keeps these markers in a state folder, apart from the activity files. On each run, the container downloads only the activities that have no marker. Therefore an application can read an activity file and then delete it. The container does not download that file again. For more information, read [Output files](#output-files).

## Quick Start (Docker Compose)

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. Set `TRACKERS` and the credentials of each tracker. For Garmin, set `GARMIN_EMAIL` and `GARMIN_PASSWORD`.

3. Run the interactive setup one time for each tracker. This step authenticates and answers the MFA challenge:

   ```bash
   docker compose run --rm -it garmin-sync python -m src.setup garmin
   ```

   This command saves the tokens in the `./tokens` volume, in one folder for each tracker.

4. Run the sync manually:

   ```bash
   docker compose run --rm garmin-sync
   ```

5. To run the sync on a schedule, add an entry to the host crontab:

   ```cron
   0 6 * * * cd /path/to/garmin-activities-download && docker compose run --rm garmin-sync
   ```

The container writes the activity files to `./data`, in one folder for each configured format. The tokens stay in `./tokens` between runs. For the file names, read [Output files](#output-files).

### File ownership on Linux

The container runs as UID/GID 1000. The files in `./data` and `./tokens` get that owner. If your host user is different, set `UID` and `GID` in `.env` to your own `id -u` and `id -g` values:

```dotenv
UID=1001
GID=1001
```

[compose.yaml](compose.yaml) applies these values at run time. The values take effect on the next run, with no rebuild. This is also true for the pre-built image from GHCR.

> [!NOTE]
> Set `UID` in `.env`, not on the command line. `bash` makes `UID` a readonly variable, so the command `UID=1001 docker compose run ...` fails.

## Kubernetes Deployment

The [k8s/cronjob.yaml](k8s/cronjob.yaml) manifest defines three objects: a PersistentVolumeClaim for the tokens, a Secret for the credentials, and a CronJob for the schedule.

The tokens of each tracker must exist in the PVC before the first scheduled run. The container runs with no user input. It cannot prompt for MFA or open a browser. Use one of these two procedures:

* Run an interactive pod that mounts the same PVC. Then run the setup in that pod, one time for each tracker:

  ```bash
  kubectl run garmin-setup --rm -it --image=ghcr.io/OWNER/garmin-activities-download:latest \
    --overrides='{"spec":{"containers":[{"name":"garmin-setup","image":"ghcr.io/OWNER/garmin-activities-download:latest","command":["python","-m","src.setup","garmin"],"stdin":true,"tty":true,"volumeMounts":[{"name":"tokens","mountPath":"/app/tokens"}]}],"volumes":[{"name":"tokens","persistentVolumeClaim":{"claimName":"garmin-tokens-pvc"}}]}}'
  ```

  For Wahoo, replace `garmin` with `wahoo` in the `command` list. The Wahoo setup also needs `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET` in the pod environment.

* Run the setup locally against the `./tokens` directory. Then copy the token files into the PVC with `kubectl cp`, from a temporary pod that mounts the PVC.

When the tokens are in the PVC, apply the manifest:

```bash
kubectl apply -f k8s/cronjob.yaml
```

Before you apply the manifest, set the credentials of each tracker in the Secret. Also set the container image reference. The manifest includes a `hostPath` volume for the output data. The comments give a PVC alternative for clusters with more than one node.

The pod runs as UID/GID 1000 through `securityContext`. The `fsGroup` value makes the kubelet change the group of the token PVC, so the non-root process can write to it. If your storage needs a different owner, change `runAsUser` and `runAsGroup`.

> [!IMPORTANT]
> `fsGroup` does not apply to `hostPath` volumes. `DirectoryOrCreate` creates the output path with the owner `root:root`, and the non-root container cannot write to it. If you use Option A, create the directory on the node first:
>
> ```bash
> sudo mkdir -p /data/garmin-gpx && sudo chown 1000:1000 /data/garmin-gpx
> ```
>
> Option B (the PVC) does not need this step.

## Configuration

| Variable | Default | Description |
|----------|---------|--------------|
| `TRACKERS` | `garmin` | Comma-separated list of trackers to download from. Each entry is `garmin` or `wahoo` |
| `DAYS_BACK` | `7` | Number of past days of activities that each run downloads |
| `TOKENS_DIR` | `/app/tokens` | Path where the container reads and writes the authentication tokens. Each tracker uses its own folder below this path |
| `OUTPUT_DIR` | `/app/data` | Path where the container saves the activity files, in one folder for each format |
| `STATE_DIR` | `<OUTPUT_DIR>/.state` | Path where the container writes the deduplication markers. One empty marker for each activity file |
| `DOWNLOAD_TARGETS` | `FIT` | Comma-separated list of destinations. Each entry is `FIT`, `GPX`, or `TCX`, or `folder=FORMAT+FORMAT` |
| `<TRACKER>_DOWNLOAD_TARGETS` | — | Destinations for one tracker only, for example `GARMIN_DOWNLOAD_TARGETS`. Replaces `DOWNLOAD_TARGETS` for that tracker |
| `DOWNLOAD_FORMATS` | — | **Deprecated.** The earlier variable, with `FORMAT:subfolder` entries. Read [Download targets](#download-targets) |

Each tracker has its own credential variables. Read [Trackers](#trackers) for these.

All credential variables also accept Docker secrets. Put the value in `/run/secrets/<variable name in lower case>`. For example, `GARMIN_EMAIL` reads `/run/secrets/garmin_email`. The container reads these files first, and the environment variables second.

`DAYS_BACK` must be an integer. Each `TRACKERS` entry must name a known tracker. Each `DOWNLOAD_TARGETS` entry must name `FIT`, `GPX`, or `TCX`. The container validates these variables at startup. An invalid value stops the run with exit code `2`, before the first download.

## Trackers

`TRACKERS` selects the trackers of a run. The container runs them in the given order. Names are not case-sensitive, and the container ignores a repeated name.

```dotenv
TRACKERS=garmin,wahoo
```

Each tracker downloads only the formats that it supplies. If `DOWNLOAD_TARGETS` asks for a format that a tracker does not supply, that tracker skips the format and writes a warning. The other trackers are not affected. But if `<TRACKER>_DOWNLOAD_TARGETS` asks that tracker for the format by name, the container stops at startup with exit code `2`.

| Tracker | Formats | Credentials | First setup |
|---------|---------|-------------|-------------|
| `garmin` | `FIT`, `GPX`, `TCX` | `GARMIN_EMAIL`, `GARMIN_PASSWORD` (both optional if the tokens are valid) | `python -m src.setup garmin` |
| `wahoo` | `FIT` | `WAHOO_CLIENT_ID`, `WAHOO_CLIENT_SECRET` (both necessary for every run) | `python -m src.setup wahoo` |

If one tracker fails, the container continues with the other trackers. The exit code reports the most serious failure. Read [Exit codes](#exit-codes).

### Garmin

Set `GARMIN_EMAIL` and `GARMIN_PASSWORD`, then run the setup one time:

```bash
docker compose run --rm -it garmin-sync python -m src.setup garmin
```

The setup asks for the email, the password, and the MFA code. It then saves the tokens to `<TOKENS_DIR>/garmin`. The credentials are optional after this step. If the saved tokens fail, the container uses the credentials again.

### Wahoo

Wahoo uses OAuth2. The container cannot open a browser, so the first authentication needs some manual steps.

**Before you start**, do these steps one time:

1. Register an application at the Wahoo developer portal. Make a note of the client ID and the client secret.
2. Register a redirect URI for the application. It must agree exactly with `WAHOO_REDIRECT_URI`. The default value is `http://localhost`.
3. Give the application the scopes `user_read`, `workouts_read`, and `offline_data`.
4. Put the client ID and the client secret in `.env` as `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET`.

> [!IMPORTANT]
> Wahoo needs `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET` for **every** run, not only for the setup. The container uses them to refresh the access token. This is different from Garmin, where the credentials are only a fallback.

Then run the setup:

```bash
docker compose run --rm -it garmin-sync python -m src.setup wahoo
```

The setup prints an authorization URL. Do these steps:

1. Open the URL in a browser and approve the access.
2. The browser goes to `http://localhost` and fails to load the page. This is correct. No program listens at that address.
3. Copy the value of `code` from the address bar of the browser.
4. Paste the value at the prompt.

The setup saves the tokens to `<TOKENS_DIR>/wahoo/tokens.json`.

Wahoo gives one activity file for each workout, and it is always a FIT file. Therefore the Wahoo tracker skips `GPX` and `TCX`. A workout with no recorded file, such as a planned workout, is also skipped.

> [!NOTE]
> From 1 January 2026, Wahoo permits 10 unrevoked access tokens for each user. Each setup run uses one of these. The container refreshes a token only immediately before it reads the API, so a normal run does not waste tokens. If you get a token limit error, send `DELETE https://api.wahooligan.com/v1/permissions` to remove the application access, then run the setup again.

### Download targets

A download target is a destination folder and the formats that go into it. A destination usually belongs to one downstream application, not to one format. Therefore a destination can hold more than one format, and it keeps its name when you add a format to it.

An entry has this form:

```
folder=FORMAT[+FORMAT]
```

A format name alone is a short form for a folder with the same name. `DOWNLOAD_TARGETS=FIT,GPX` fills `data/FIT` and `data/GPX`. This is the default layout.

```bash
DOWNLOAD_TARGETS=FIT, strava-inbox=GPX, app2=GPX+FIT
```

This example fills `data/FIT` with FIT files, `data/strava-inbox` with GPX files, and `data/app2` with GPX files and FIT files. The container downloads each activity from the tracker one time for each format, independent of the number of destinations.

A destination folder can have more than one level:

```bash
DOWNLOAD_TARGETS=GPX/you@example.com=GPX
```

#### Placeholders

A destination can contain `{format}` or `{tracker}`. The container replaces them at startup, and one entry then fills more than one folder:

| Entry | Result with `TRACKERS=garmin,wahoo` |
|-------|--------------------------------------|
| `archive/{format}=GPX+FIT` | `data/archive/GPX`, `data/archive/FIT` |
| `{tracker}/{format}=FIT` | `data/garmin/FIT`, `data/wahoo/FIT` |

#### One tracker only

`DOWNLOAD_TARGETS` applies to all trackers. To give one tracker different destinations, set `<TRACKER>_DOWNLOAD_TARGETS`. This variable replaces `DOWNLOAD_TARGETS` for that tracker. It does not add to it.

```bash
TRACKERS=garmin,wahoo
DOWNLOAD_TARGETS=FIT
GARMIN_DOWNLOAD_TARGETS=FIT, GPX/you@example.com=GPX
```

Garmin fills `data/FIT` and `data/GPX/you@example.com`. Wahoo fills `data/FIT` only.

A tracker with no variable of its own uses `DOWNLOAD_TARGETS`. A variable for a tracker that is not in `TRACKERS` is ignored, with a warning in the log.

#### Rules

* A destination folder is a relative path below `OUTPUT_DIR`. It cannot start with `/`, and no part of it can be `.` or `..`.
* Format names are case-insensitive. Folder names are used exactly as written.
* A repeated format and folder pair is permitted. The container ignores the duplicate and fills the folder one time.
* Two formats in one folder do not collide, because the extension is different.
* A tracker that cannot supply a format skips it, with a warning. But if `<TRACKER>_DOWNLOAD_TARGETS` asks for that format by name, the container stops with exit code `2`. For example, Wahoo supplies FIT only, so `WAHOO_DOWNLOAD_TARGETS=app2=GPX` is an error.

#### DOWNLOAD_FORMATS (deprecated)

`DOWNLOAD_FORMATS` continues to work with no change, and the container writes the same files as before. Its entries have a different form: `FORMAT:subfolder` puts the subfolder below the format folder, so `FIT:archive` fills `data/FIT/archive`.

Set `DOWNLOAD_FORMATS` or `DOWNLOAD_TARGETS`, but not both. If both are set, the container stops with exit code `2`. To move to the new variable, write each old entry as a folder:

| Old | New |
|-----|-----|
| `DOWNLOAD_FORMATS=FIT,GPX` | `DOWNLOAD_TARGETS=FIT,GPX` |
| `DOWNLOAD_FORMATS=FIT:archive` | `DOWNLOAD_TARGETS=FIT/archive=FIT` |
| `DOWNLOAD_FORMATS=FIT,FIT:archive,GPX:you@example.com` | `DOWNLOAD_TARGETS=FIT,FIT/archive=FIT,GPX/you@example.com=GPX` |

Keep the same folder names during the move. The files and the markers then stay where they are, and the container downloads nothing again.

## Output files

The container saves each activity as `<OUTPUT_DIR>/<destination>/<tracker>-<activityId>.<extension>`. The name is the name of the tracker and the activity ID from that tracker, not the date or the name of the activity. The destination comes from `DOWNLOAD_TARGETS`. Read [Download targets](#download-targets).

The tracker name is part of the file name because two trackers can give the same activity ID. Without it, one tracker could write over the file of another tracker, and deduplication could skip a new activity.

This example uses `TRACKERS=garmin,wahoo` and `DOWNLOAD_TARGETS=FIT,GPX,TCX`:

```
data/
├── FIT
│   ├── garmin-17284419021.fit
│   └── wahoo-4471.fit
├── GPX/garmin-17284419021.gpx
└── TCX/garmin-17284419021.tcx
```

Wahoo supplies only FIT, so `data/GPX` and `data/TCX` hold Garmin files only.

This example feeds two applications. Each one has its own folder, and the second one takes two formats:

```bash
TRACKERS=garmin
DOWNLOAD_TARGETS=FIT, strava-inbox=FIT, app2=GPX+FIT
```

```
data/
├── FIT/garmin-17284419021.fit
├── strava-inbox/garmin-17284419021.fit
└── app2
    ├── garmin-17284419021.gpx
    └── garmin-17284419021.fit
```

The container gets each format from the tracker one time. It then writes that one file to each destination that does not have it.

Deduplication uses only the filesystem. There is no database and no manifest. For each activity file, the container writes an empty marker file with the same name in the state folder. Before each download, the container looks for the marker. If the marker is there, the container skips that activity.

The state folder is `<OUTPUT_DIR>/.state` by default. It holds the same folder structure as the data folder. If an application reads `<OUTPUT_DIR>` and all its subfolders, set `STATE_DIR` to a path outside the data folder.

```
data/
├── .state
│   └── FIT/garmin-17284419021.fit
└── FIT/garmin-17284419021.fit
```

The marker makes the activity file itself removable. An application can read a file and then delete it, and the container does not download that file again. Deduplication that reads the activity file instead downloads every activity in the `DAYS_BACK` window again on each run.

If the activity file is in its folder but the marker is absent, the container skips that activity and writes the marker. Therefore an upgrade from an earlier version does not download the existing files again. To download a file again, delete its marker. Read [Troubleshooting](#troubleshooting).

The downloader waits one second between downloads to stay below the rate limits of the tracker. This delay is not configurable. If `DAYS_BACK` is large and there are several formats, the first run is slow. Expect approximately one second for each file. The container logs the progress for each activity.

### Unsafe activity IDs

The activity ID that names each file comes directly from the API response of the tracker. The container validates the ID before it builds a path from it: the ID must contain only ASCII letters and digits. The trackers return plain numbers today. The container accepts letters, so a future ID scheme continues to work with no code change. The container rejects all other values, such as a path separator, a `..` segment, or an absolute path.

An invalid ID stops that tracker with exit code `3` and logs the bad value. The container writes no more files for that tracker, also not for the later activities in the same batch. The other trackers continue. A normal run cannot cause this error. The error shows that the response is not the unmodified response of the tracker.

If this error occurs, examine what is between the container and the API of the tracker. The usual causes are an intercepting proxy, a DNS or TLS error, or a modified client library.

## Exit codes

The container runs one time and exits. It runs on a schedule with no operator, so use the exit code to decide whether a run needs attention.

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success. The container downloaded the new activities, or there was nothing new | None |
| `1` | Authentication failed, and the credentials did not work | Run the interactive setup again for the tracker in the log (see below) |
| `2` | Other error, for example an invalid configuration or a tracker API error | Read the logs. This error is often temporary and the next scheduled run corrects it |
| `3` | A tracker returned an activity ID that is not alphanumeric, so the container built no output path | Read the logs. See [Unsafe activity IDs](#unsafe-activity-ids) |

Codes `1` and `3` need an alert, because they do not correct themselves.

A failure of one tracker does not stop the other trackers. An expired Wahoo token must not cost a scheduled run its Garmin activities. If more than one tracker fails, the container reports the most serious code, in this sequence: `3`, then `1`, then `2`. Each log line starts with the name of its tracker, so you can find which tracker failed.

## Troubleshooting

**Garmin authentication fails on a scheduled run (exit code `1`).** Tokens do not usually need manual work. When the expiry time is near, `garminconnect` refreshes them at each login. It then writes the new tokens to the token store. Therefore scheduled runs stay authenticated. Exit code `1` shows that something broke the token store. The usual causes are:

* A change of the account password.
* A sign-out that revoked the session.
* A token directory that did not persist between runs.
* A long interval between runs, so the credential expired.

The container then uses `GARMIN_EMAIL` and `GARMIN_PASSWORD`. But these credentials cannot answer an MFA challenge with no user input. Run the interactive setup again to get a new token set:

```bash
docker compose run --rm -it garmin-sync python -m src.setup garmin
```

On Kubernetes, run the setup again with one of the procedures in [Kubernetes Deployment](#kubernetes-deployment). The new tokens then go into the PVC.

**Wahoo authentication fails on a scheduled run (exit code `1`).** A Wahoo access token is valid for 2 hours, so a daily run always refreshes it. The refresh token has no expiry time, and the container saves the new one at each refresh. Therefore a daily schedule continues to work with no manual step. Exit code `1` for Wahoo usually shows one of these:

* `WAHOO_CLIENT_ID` or `WAHOO_CLIENT_SECRET` is absent. The container needs both for every run.
* The token file did not persist between runs, or a different run wrote over it.
* The user revoked the application access.
* The account is at the limit of 10 unrevoked access tokens.

Run `python -m src.setup wahoo` again to get a new token set. If the token limit is the cause, first send `DELETE https://api.wahooligan.com/v1/permissions`.

**You need an activity file again.** The container does not download an activity file again when the marker of that file is in the state folder. This is correct for an application that deletes each file after it reads the file. If you delete a file by accident, or if a file is damaged, delete its marker. The next run downloads that file again.

The marker has the same path and the same name as the activity file, below the state folder. For the file `data/FIT/garmin-17284419021.fit`, the marker is `data/.state/FIT/garmin-17284419021.fit`.

```bash
rm data/.state/FIT/garmin-17284419021.fit
```

To download all the files of one folder again, delete that folder below the state folder:

```bash
rm -rf data/.state/GPX/you@example.com
```

CAUTION: If you delete an activity file but keep its marker, the container does not download that file again. Delete the marker also.

The activity must be in the `DAYS_BACK` window. If the activity is older than this window, increase `DAYS_BACK` for one run.

**The container downloads activities again after a reorganization.** Each marker matches one exact path, `<STATE_DIR>/<destination>/<tracker>-<activityId>.<ext>`. A move of the activity files alone is safe, because the markers do not change. But if you rename a destination in `DOWNLOAD_TARGETS`, the new destination has no markers, and the container fills it again. Rename the folder below the state folder in the same way, or accept the one large run. Do not increase `DAYS_BACK`.

```bash
mv data/.state/old-name data/.state/new-name
```

**The container never downloads the older activities.** `DAYS_BACK` limits every run, so the container ignores all activities that are older than this window. To backfill, run the container one time with a larger value.

## Development

### Dev Container (recommended)

This repository includes a [Dev Container](https://containers.dev/) at [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json). Use it instead of the manual setup below.

1. Start Docker and install the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.
2. Open the repository folder in VS Code.
3. Run **Dev Containers: Reopen in Container** from the Command Palette (`F1`).

The first build takes some minutes. The environment is ready when `postCreateCommand` completes the installation of the dependencies.

The dev container is built from the project [Dockerfile](Dockerfile), from its `dev` stage. Development therefore uses the same `python:3.14-slim` base image and the same non-root `appuser` as the released image. The `dev` stage keeps `pip`, which the released `runtime` stage removes, so `postCreateCommand` can install the development dependencies. This base image is minimal, so [Features](https://containers.dev/features) add `git`, the GitHub CLI, and a configured shell.

The workspace mounts at `/workspaces/garmin-activities-download`, not at `/app` as in the image. Therefore `OUTPUT_DIR` and `TOKENS_DIR` point to the `./data` and `./tokens` folders in the repository. An interactive `python -m src.setup <tracker>` or `python -m src.main` in the container reads and writes these directories.

You do not need the `UID` and `GID` values from [File ownership on Linux](#file-ownership-on-linux). On Linux, `updateRemoteUserUID` maps `appuser` to your host user when the container is created. Workspace files that you create keep you as the owner.

> [!NOTE]
> The dev container has no Docker CLI and no mounted Docker socket. You cannot run `docker build` or `docker compose` in it. Run these commands in a terminal on the host.

### Manual setup

If you work outside a container, install the runtime and development dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Run the test suite with coverage:

```bash
pytest --cov
```

Lint the code and control the format:

```bash
ruff check .
ruff format --check .
```

Build the container image locally:

```bash
docker build -t garmin-activities-download:test .
```

The [Dockerfile](Dockerfile) has three stages. `builder` installs [requirements.txt](requirements.txt) into a virtual environment at `/opt/venv`. `runtime` is the default target and the shipped image: it copies that environment in and deletes the build-time parts of the base image, `pip` and `ensurepip` above all. Those two bundle their own vendored dependency set, which an image scanner reports as installed packages even though nothing in `src/` imports them. `dev` is the dev container target described above.

### Add a tracker

A tracker is one module in [src/trackers/](src/trackers/). To add one:

1. Write `src/trackers/<name>.py` with a subclass of `Tracker` from [src/trackers/base.py](src/trackers/base.py). Give it a `name`, a `supported_formats` set, and the five methods: `from_env`, `authenticate`, `list_activities`, `download`, and `interactive_setup`.
2. Add the class to `_REGISTERED` in [src/trackers/\_\_init\_\_.py](src/trackers/__init__.py).
3. Add tests as `tests/test_trackers_<name>.py`.
4. Add a row to the table in [Trackers](#trackers).

No other code changes. `download` must return the final bytes of the file, so a tracker that gets an archive must unpack it. Keep the module free of input and output at import time, because CI imports it as a smoke test.

## CI/CD

The [.github/workflows/ci.yml](.github/workflows/ci.yml) workflow runs on pushes and pull requests to `main`, and on `v*.*.*` tags. The workflow has five jobs:

* `lint` and `test` run in parallel.
* `build-push` builds the image and smoke tests it.
* `security-scan` runs a Trivy vulnerability scan (`CRITICAL` and `HIGH`). It prints the findings to the job log, then repeats the scan to upload the results to GitHub code scanning and to fail the build on anything fixable.
* `release` publishes a GitHub release.

The event controls how many of these jobs run:

| Event | Jobs that run |
|-------|---------------|
| Pull request | `lint`, `test`, and `build-push`. The workflow builds the image but does not push it, so `security-scan` and `release` do not run |
| Push to `main` | The jobs above. The workflow also pushes the image to GHCR with an attestation, then runs `security-scan` |
| `v*.*.*` tag | All five jobs, with a published GitHub release at the end |

To make a release, tag the commit and push the tag:

```bash
git tag v1.0.0
git push --tags
```

## License

Released under the MIT License. See [LICENSE](LICENSE).
