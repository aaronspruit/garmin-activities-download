
# Garmin Activities Download

This container downloads GPS activities as FIT, GPX, or TCX files. It supports Garmin Connect and Wahoo. It runs one time, then it exits. You can deploy it with Docker Compose or with a Kubernetes CronJob.

## Overview

The container connects to each configured tracker, downloads the recent activities, then exits. This run-once design fits a Kubernetes CronJob or a host crontab entry. The container is not a long-running service.

The `TRACKERS` variable selects the trackers. Each tracker keeps its own tokens and reads its own credentials. If one tracker fails, the other trackers continue.

The authentication tokens stay on disk between runs. Each tracker needs an interactive setup one time. Every scheduled run after that uses the saved tokens with no user input.

The container writes a marker file for each activity file that it downloads, in a separate state folder. Each run downloads only the activities that have no marker. An application can therefore read an activity file and then delete it, and the container does not download that file again. For more information, read [Output files](#output-files).

## Quick start

### Docker Compose

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. Set `TRACKERS` and the credentials of each tracker. For Garmin, set `GARMIN_EMAIL` and `GARMIN_PASSWORD`.

3. Run the interactive setup one time for each tracker. This step authenticates and answers the MFA challenge:

   ```bash
   docker compose run --rm -it garmin-sync python -m src.setup garmin
   ```

   The command saves the tokens in the `./tokens` volume, in one folder for each tracker.

4. Run the sync manually:

   ```bash
   docker compose run --rm garmin-sync
   ```

5. To run the sync on a schedule, add an entry to the host crontab:

   ```cron
   0 6 * * * cd /path/to/garmin-activities-download && docker compose run --rm garmin-sync
   ```

The container writes the activity files to `./data`, in one folder for each destination. The tokens stay in `./tokens` between runs.

#### File ownership on Linux

The container runs as UID/GID 1000, and the files in `./data` and `./tokens` get that owner. If your host user is different, set `UID` and `GID` in `.env` to your own `id -u` and `id -g` values:

```dotenv
UID=1001
GID=1001
```

[compose.yaml](compose.yaml) applies these values at run time, with no rebuild. This also works with the pre-built image from GHCR.

> [!NOTE]
> Set `UID` in `.env`, not on the command line. `bash` makes `UID` a readonly variable, so the command `UID=1001 docker compose run ...` fails.

### Kubernetes CronJob

[k8s/cronjob.yaml](k8s/cronjob.yaml) defines three objects: a PersistentVolumeClaim for the tokens, a Secret for the credentials, and a CronJob for the schedule.

The tokens of each tracker must exist in the PVC before the first scheduled run. The container runs with no user input, so it cannot prompt for MFA or open a browser. Use one of these two procedures:

* Run an interactive pod that mounts the same PVC, then run the setup in that pod, one time for each tracker:

  ```bash
  kubectl run garmin-setup --rm -it --image=ghcr.io/OWNER/garmin-activities-download:latest \
    --overrides='{"spec":{"containers":[{"name":"garmin-setup","image":"ghcr.io/OWNER/garmin-activities-download:latest","command":["python","-m","src.setup","garmin"],"stdin":true,"tty":true,"volumeMounts":[{"name":"tokens","mountPath":"/app/tokens"}]}],"volumes":[{"name":"tokens","persistentVolumeClaim":{"claimName":"garmin-tokens-pvc"}}]}}'
  ```

  For Wahoo, replace `garmin` with `wahoo` in the `command` list. The Wahoo setup also needs `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET` in the pod environment.

* Run the setup locally against the `./tokens` directory. Then copy the token files into the PVC with `kubectl cp`, from a temporary pod that mounts the PVC.

Before you apply the manifest, set the credentials of each tracker in the Secret, and set the container image reference. When the tokens are in the PVC, apply the manifest:

```bash
kubectl apply -f k8s/cronjob.yaml
```

The manifest includes a `hostPath` volume for the output data. Its comments give a PVC alternative for clusters with more than one node.

The pod runs as UID/GID 1000 through `securityContext`. The `fsGroup` value makes the kubelet change the group of the token PVC, so the non-root process can write to it. If your storage needs a different owner, change `runAsUser` and `runAsGroup`.

> [!IMPORTANT]
> `fsGroup` does not apply to `hostPath` volumes. `DirectoryOrCreate` creates the output path with the owner `root:root`, and the non-root container cannot write to it. If you use the `hostPath` option, create the directory on the node first:
>
> ```bash
> sudo mkdir -p /data/garmin-gpx && sudo chown 1000:1000 /data/garmin-gpx
> ```
>
> The PVC option does not need this step.

## Configuration

### Variables

| Variable | Default | Description |
|----------|---------|--------------|
| `TRACKERS` | `garmin` | Comma-separated list of trackers to download from. Each entry is `garmin` or `wahoo` |
| `DAYS_BACK` | `7` | Number of past days of activities that each run downloads |
| `TOKENS_DIR` | `/app/tokens` | Path where the container reads and writes the authentication tokens. Each tracker uses its own folder below this path |
| `OUTPUT_DIR` | `/app/data` | Path where the container saves the activity files |
| `STATE_DIR` | `<OUTPUT_DIR>/.state` | Path where the container writes the deduplication markers. One empty marker for each activity file |
| `DOWNLOAD_TARGETS` | `FIT` | Comma-separated list of destinations. Each entry is `FIT`, `GPX`, or `TCX`, or `folder=FORMAT+FORMAT` |
| `<TRACKER>_DOWNLOAD_TARGETS` | — | Destinations for one tracker only, for example `GARMIN_DOWNLOAD_TARGETS`. Replaces `DOWNLOAD_TARGETS` for that tracker |
| `WAHOO_APP_TIER` | `sandbox` | Tier of your Wahoo application registration, `sandbox` or `production`. Set it to the tier that you asked Wahoo for. Read [Rate limits](#rate-limits) |

Seven more variables control the rate limits. Read [Rate limits](#rate-limits).

| Variable for every tracker | Variable for one tracker | Default | Description |
|----------------------------|--------------------------|---------|--------------|
| `MAX_DOWNLOADS_PER_RUN` | `<TRACKER>_MAX_DOWNLOADS_PER_RUN` | `0` | Number of files that one run writes before it stops. `0` removes the cap. It counts files, not activities, so an activity with 3 formats counts 3 times. The run compares the count between activities, so the last activity writes all of its formats and the run can finish above this number |
| `RATE_LIMIT_MAX_WAIT` | `<TRACKER>_MAX_WAIT` | `300` | Longest single wait in seconds that a run accepts. A longer wait stops the run |
| `MAX_RETRIES` | `<TRACKER>_MAX_RETRIES` | 2 for `garmin`, 3 for `wahoo` | Number of retries after a failure that can clear, which is a rate limit refusal or a server failure. The container does not retry a refused credential or a missing file |
| `BACKOFF_INITIAL` | `<TRACKER>_BACKOFF_INITIAL` | 30 for `garmin`, 5 for `wahoo` | Delay in seconds before the first retry. It doubles for each retry that follows |
| `BACKOFF_MAX` | `<TRACKER>_BACKOFF_MAX` | `300` | Largest delay in seconds that the backoff produces |
| — | `<TRACKER>_RATE_LIMIT` | see [Rate limits](#rate-limits) | Limits of that API, as `REQUESTS/SECONDS` entries, for example `20/60, 300/3600`. The value `none` removes these windows. It does not remove `<TRACKER>_MIN_INTERVAL`, which still paces the requests |
| — | `<TRACKER>_MIN_INTERVAL` | 2 for `garmin`, 0.5 for `wahoo` | Smallest gap in seconds between two requests to that tracker |

The last two describe the API of one tracker, so they have no form for every tracker.

A typical `.env`:

```dotenv
TRACKERS=garmin,wahoo
DAYS_BACK=7
DOWNLOAD_TARGETS=FIT, strava-inbox=GPX
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
WAHOO_CLIENT_ID=your-client-id
WAHOO_CLIENT_SECRET=your-client-secret
```

Each tracker has its own credential variables. Read [Trackers](#trackers) for these.

All credential variables also accept Docker secrets. Put the value in `/run/secrets/<variable name in lower case>`. For example, `GARMIN_EMAIL` reads `/run/secrets/garmin_email`. The container reads these files first, and the environment variables second.

The container validates the variables at startup. `DAYS_BACK` must be an integer. Each `TRACKERS` entry must name a known tracker. Each `DOWNLOAD_TARGETS` entry must name `FIT`, `GPX`, or `TCX`. An invalid value stops the run with exit code `2`, before the first download.

### Download targets

A download target is a destination folder and the formats that go into it. A destination usually belongs to one downstream application, not to one format. A destination can therefore hold more than one format, and it keeps its name when you add a format to it.

An entry has this form:

```
folder=FORMAT[+FORMAT]
```

A format name alone is a short form for a folder with the same name. `DOWNLOAD_TARGETS=FIT,GPX` fills `data/FIT` and `data/GPX`. This is the default layout.

```bash
DOWNLOAD_TARGETS=FIT, strava-inbox=GPX, app2=GPX+FIT
```

This example fills `data/FIT` with FIT files, `data/strava-inbox` with GPX files, and `data/app2` with GPX files and FIT files. The container downloads each format from the tracker one time for each activity, independent of the number of destinations.

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

#### Destinations for one tracker

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

### Trackers

`TRACKERS` selects the trackers of a run, and the container runs them in the given order. Names are not case-sensitive, and the container ignores a repeated name.

```dotenv
TRACKERS=garmin,wahoo
```

| Tracker | Formats | Credentials | First setup |
|---------|---------|-------------|-------------|
| `garmin` | `FIT`, `GPX`, `TCX` | `GARMIN_EMAIL`, `GARMIN_PASSWORD` (both optional if the tokens are valid) | `python -m src.setup garmin` |
| `wahoo` | `FIT` | `WAHOO_CLIENT_ID`, `WAHOO_CLIENT_SECRET` (both necessary for every run) | `python -m src.setup wahoo` |

Each tracker downloads only the formats that it supplies. If one tracker fails, the container continues with the other trackers, and reports the most serious exit code.

#### Garmin

Set `GARMIN_EMAIL` and `GARMIN_PASSWORD`, then run the setup one time:

```bash
docker compose run --rm -it garmin-sync python -m src.setup garmin
```

The setup asks for the email, the password, and the MFA code. It then saves the tokens to `<TOKENS_DIR>/garmin`. The credentials are optional after this step. If the saved tokens fail, the container uses the credentials again.

#### Wahoo

Wahoo uses OAuth2. The container cannot open a browser, so the first authentication needs some manual steps.

**Before you start**, do these steps one time:

1. Register an application at the [Wahoo developer portal](https://developers.wahooligan.com/cloud). Make a note of the client ID and the client secret. The only difference between `Sandbox` and `Production` is the [rate limits](https://cloud-api.wahooligan.com/#registration). For personal use, `Sandbox` is enough.
2. Register a redirect URI for the application. It must agree exactly with `WAHOO_REDIRECT_URI`. The default value is `https://localhost`. Wahoo does not accept an `http://` address.
3. Give the application the scopes `user_read`, `workouts_read`, and `offline_data`.
4. Put the client ID and the client secret in `.env` as `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET`.
5. Ask Wahoo to approve the application for the Cloud API. Read [Wahoo application approval](#wahoo-application-approval).

> [!IMPORTANT]
> Wahoo needs `WAHOO_CLIENT_ID` and `WAHOO_CLIENT_SECRET` for **every** run, not only for the setup. The container uses them to refresh the access token. This is different from Garmin, where the credentials are only a fallback.

Then run the setup:

```bash
docker compose run --rm -it garmin-sync python -m src.setup wahoo
```

The setup prints an authorization URL. Do these steps:

1. Open the URL in a browser and approve the access.
2. The browser goes to `https://localhost` and fails to load the page. This is correct. No program listens at that address.
3. Copy the value of `code` from the address bar of the browser.
4. Paste the value at the prompt.

The setup saves the tokens to `<TOKENS_DIR>/wahoo/tokens.json`.

Wahoo gives one activity file for each workout, and it is always a FIT file. The Wahoo tracker therefore skips `GPX` and `TCX`. A workout with no recorded file, such as a planned workout, is also skipped.

> [!NOTE]
> The setup completes before Wahoo approves the application, but the API then refuses every call with status 422. Do not put `wahoo` in `TRACKERS` until the approval is granted.

> [!NOTE]
> From 1 January 2026, Wahoo permits 10 unrevoked access tokens for each user. Each setup run uses one of these. The container refreshes a token only immediately before it reads the API, so a normal run does not waste tokens. If you get a token limit error, send `DELETE https://api.wahooligan.com/v1/permissions` to remove the application access, then run the setup again.

## Output files

The container saves each activity as `<OUTPUT_DIR>/<destination>/<tracker>-<activityId>.<extension>`. The name holds the name of the tracker and the activity ID from that tracker, not the date or the name of the activity. Two trackers can give the same activity ID, so without the tracker name one tracker could write over the file of another.

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

The container gets each format from the tracker one time, then writes that one file to each destination that does not have it.

Deduplication uses only the filesystem. There is no database and no manifest. For each activity file, the container writes an empty marker file with the same name in the state folder. Before each download, the container looks for the marker. If the marker is there, the container skips that activity.

The state folder is `<OUTPUT_DIR>/.state` by default, and it holds the same folder structure as the data folder:

```
data/
├── .state
│   └── FIT/garmin-17284419021.fit
└── FIT/garmin-17284419021.fit
```

If an application reads `<OUTPUT_DIR>` and all its subfolders, set `STATE_DIR` to a path outside the data folder.

The marker makes the activity file itself removable. An application can read a file and then delete it, and the container does not download that file again. If the activity file is in its folder but the marker is absent, the container writes the marker and downloads nothing. To download a file again, delete its marker. Read [You need an activity file again](#you-need-an-activity-file-again).

## Rate limits

Each tracker paces its own requests, retries the failures that pass, and stops the run when it reaches a limit. The container never waits out a long limit. If the next request needs more than `RATE_LIMIT_MAX_WAIT` seconds, the run stops early.

Where the run stops decides what it can report. If a limit stops a download, the container knows the list of activities, so it writes a log line that names how many it did not reach. If a limit stops the list itself, there is no such number, and the container reports only that it downloaded nothing.

An early stop is a success, and the exit code stays `0`. Every file that the run wrote already has its marker, so the next scheduled run continues at the same activity. An account with thousands of activities therefore fills across several runs.

`RATE_LIMIT_MAX_WAIT` bounds one wait, not the run. A retry waits for the backoff, and the backoff never grows above `BACKOFF_MAX`, so a run stops on a retry only when `BACKOFF_MAX` is larger than `RATE_LIMIT_MAX_WAIT`. With the default values the two are equal, and a run therefore stops for one reason only: a window that is full, or a wait that the tracker itself asked for. The retries then end after `MAX_RETRIES`, which bounds the run at `MAX_RETRIES` × `BACKOFF_MAX` seconds for each request.

### The limits of each tracker

| Tracker | Limits | Source | What counts |
|---------|--------|--------|-------------|
| `garmin` | 20 requests per minute, 300 per hour, 2000 per day, 2 seconds between requests | A conservative default. Garmin publishes no limit for this API | The login, the activity list, and every file download |
| `wahoo`, `sandbox` | 25 requests per 5 minutes, 100 per hour, 250 per day | Published by Wahoo | The workout list only |
| `wahoo`, `production` | 200 requests per 5 minutes, 1000 per hour, 5000 per day | Published by Wahoo | The workout list only |

The Wahoo tier belongs to the application registration. You ask Wahoo for a sandbox application or for a production application, and Wahoo approves that request. An application stays in the tier that you asked for. Set `WAHOO_APP_TIER` to that tier once, and leave it. Sandbox is the default, and the sandbox limits are enough for one person.

Wahoo exempts the authentication, the token refresh, and the file downloads from its limits. A Wahoo run therefore spends its budget on the list pages alone, and it downloads the files at full speed. Wahoo also reports the count that is left in the headers of each response, and the container obeys those headers.

One Wahoo list page holds 30 workouts, and the container reads at most 100 pages. A window of 3000 workouts therefore costs 100 requests to list, which is the whole hourly budget of a sandbox application. If a limit stops the list at page 50, the container keeps the 1470 workouts of the 49 pages that it read, and it downloads them, because the files cost nothing. The run is never wasted. A window that needs more pages than the budget allows is still a problem, because each run starts the list again at page 1. To fill a long history, raise `DAYS_BACK` in steps instead of in one jump.

Garmin publishes nothing, so its numbers are a judgement, not a fact. They are low on purpose. A Garmin refusal applies to the account, not to the address, and it has locked users out for 24 to 48 hours. Watch your own account for some weeks, then raise the numbers with `GARMIN_RATE_LIMIT` if you need a faster backfill.

### To change the limits

```dotenv
# The Wahoo application is registered as a production application.
WAHOO_APP_TIER=production

# Garmin accepts more than the default on this account.
GARMIN_RATE_LIMIT=40/60, 600/3600, 4000/86400
GARMIN_MIN_INTERVAL=1.0

# Keep each run inside a 30 minute schedule interval.
MAX_DOWNLOADS_PER_RUN=400
RATE_LIMIT_MAX_WAIT=120
```

## Errors and troubleshooting

### Exit codes

The container runs on a schedule with no operator, so use the exit code to decide whether a run needs attention.

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success. The container downloaded the new activities, or there was nothing new, or a rate limit stopped the run early | None. If a rate limit stopped the run, the next run continues. Read [Rate limits](#rate-limits) |
| `1` | The tracker refused the credentials or the application, and there was no fallback | Read the log line of that tracker. Usually you must run the interactive setup again |
| `2` | Other error, for example an invalid configuration or a tracker API error | Read the logs. This error is often temporary, and the next scheduled run corrects it |
| `3` | A tracker returned an activity ID that is not alphanumeric | Read the logs and [Unsafe activity IDs](#unsafe-activity-ids) |

Codes `1` and `3` need an alert, because they do not correct themselves.

A failure of one tracker does not stop the other trackers. If more than one tracker fails, the container reports the most serious code, in this sequence: `3`, then `1`, then `2`. Each log line starts with the name of its tracker, so you can find which tracker failed.

### Garmin authentication fails on a scheduled run (exit code 1)

Tokens do not usually need manual work. When the expiry time is near, `garminconnect` refreshes them at each login and writes the new tokens to the token store. Exit code `1` shows that something broke the token store. The usual causes are:

* A change of the account password.
* A sign-out that revoked the session.
* A token directory that did not persist between runs.
* A long interval between runs, so the credential expired.

The container then uses `GARMIN_EMAIL` and `GARMIN_PASSWORD`. But these credentials cannot answer an MFA challenge with no user input. Run the interactive setup again to get a new token set:

```bash
docker compose run --rm -it garmin-sync python -m src.setup garmin
```

On Kubernetes, run the setup again with one of the procedures in [Kubernetes CronJob](#kubernetes-cronjob). The new tokens then go into the PVC.

### Wahoo authentication fails on a scheduled run (exit code 1)

A Wahoo access token is valid for 2 hours, and the refresh token has no expiry time.  The container saves the new token at each refresh. A daily schedule therefore continues to work with no manual step. Exit code `1` for Wahoo usually shows one of these:

* `WAHOO_CLIENT_ID` or `WAHOO_CLIENT_SECRET` is absent. The container needs both for every run.
* The token file did not persist between runs, or a different run wrote over it.
* The user revoked the application access.
* The account is at the limit of 10 unrevoked access tokens.
* Wahoo did not approve the application for the Cloud API. The log line then contains `This application has not been approved by Wahoo Fitness`. Read [Wahoo application approval](#wahoo-application-approval).

Run `python -m src.setup wahoo` again to get a new token set. If the token limit is the cause, first send `DELETE https://api.wahooligan.com/v1/permissions`. But if the application is not approved, do not run the setup again. Only Wahoo can correct that, and each run uses one more access token.

### You need an activity file again

The container does not download an activity file again while the marker of that file is in the state folder. If you delete a file by accident, or if a file is damaged, delete its marker. The next run downloads that file again.

The marker has the same path and the same name as the activity file, below the state folder. For the file `data/FIT/garmin-17284419021.fit`, the marker is `data/.state/FIT/garmin-17284419021.fit`.

```bash
rm data/.state/FIT/garmin-17284419021.fit
```

To download all the files of one folder again, delete that folder below the state folder:

```bash
rm -rf data/.state/GPX/you@example.com
```

The activity must be in the `DAYS_BACK` window. If the activity is older than this window, increase `DAYS_BACK` for one run.

> [!CAUTION]
> If you delete an activity file but keep its marker, the container does not download that file again. Delete the marker also.

### The container downloads activities again after a rename

Each marker matches one exact path, `<STATE_DIR>/<destination>/<tracker>-<activityId>.<ext>`. A move of the activity files alone is safe, because the markers do not change. But a renamed destination in `DOWNLOAD_TARGETS` has no markers, so the container fills it again. Rename the folder below the state folder in the same way, or accept the one large run. Do not increase `DAYS_BACK`.

```bash
mv data/.state/old-name data/.state/new-name
```

### The container never downloads the older activities

`DAYS_BACK` limits every run, so the container ignores all activities that are older than this window. To backfill, run the container one time with a larger value.

### Unsafe activity IDs

The activity ID that names each file comes directly from the API response of the tracker. The container validates the ID before it builds a path from it: the ID must contain only ASCII letters and digits. It rejects all other values, such as a path separator, a `..` segment, or an absolute path.

An invalid ID stops that tracker with exit code `3` and logs the bad value. The container writes no more files for that tracker, also not for the later activities in the same batch. The other trackers continue.

A normal run cannot cause this error. The error shows that the response is not the unmodified response of the tracker. Examine what is between the container and the API of the tracker. The usual causes are an intercepting proxy, a DNS or TLS error, or a modified client library.

## Development

### Dev container (recommended)

This repository includes a [Dev Container](https://containers.dev/) at [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json). Use it instead of the manual setup below.

1. Start Docker and install the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.
2. Open the repository folder in VS Code.
3. Run **Dev Containers: Reopen in Container** from the Command Palette (`F1`).

The first build takes some minutes. The environment is ready when `postCreateCommand` completes the installation of the dependencies.

The dev container is built from the `dev` stage of the project [Dockerfile](Dockerfile). Development therefore uses the same base image and the same non-root `appuser` as the released image.

The workspace mounts at `/workspaces/garmin-activities-download`, not at `/app` as in the image. `OUTPUT_DIR` and `TOKENS_DIR` therefore point to the `./data` and `./tokens` folders in the repository. An interactive `python -m src.setup <tracker>` or `python -m src.main` in the container reads and writes these directories.

You do not need the `UID` and `GID` values from [File ownership on Linux](#file-ownership-on-linux). On Linux, `updateRemoteUserUID` maps `appuser` to your host user when the container is created.

> [!NOTE]
> The dev container has no Docker CLI and no mounted Docker socket. You cannot run `docker build` or `docker compose` in it. Run these commands in a terminal on the host.

### Dev container on Kubernetes

[.devcontainer/devcontainer.k8s.json](.devcontainer/devcontainer.k8s.json) runs the same `dev` stage as a pod, with [DevPod](https://devpod.sh). Use it when you want the workspace in a cluster instead of on your workstation.

The cluster needs a namespace, a shared home volume and a pod manifest template first. These are not specific to this repository, and they live in the `devcontainer/` folder of the homek8 repository. Install them once, then start a workspace:

```bash
devpod up . --provider kubernetes \
  --devcontainer-path .devcontainer/devcontainer.k8s.json \
  --ide vscode
```

The file differs from the local one in three points. It has no `mounts` key, because `${localEnv:HOME}` has no value without a host. It sets `updateRemoteUserUID` to `false`, because a cluster has no host user. Its `postCreateCommand` runs [.devcontainer/link-shared-home.sh](.devcontainer/link-shared-home.sh) first, which points the durable parts of `~/.claude` at the shared volume.

DevPod clones the repository into the pod and does not copy your working tree. Push your work before you start a workspace. The `tokens/` folder is also absent, so run `python -m src.setup <tracker>` again inside the pod.

### Manual setup

Install the runtime and development dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Run the test suite with coverage. Coverage must stay at 75% or more:

```bash
pytest --cov
```

Lint the code and control the format. CI runs both, and both must pass:

```bash
ruff check .
ruff format --check .
```

Build the container image locally:

```bash
docker build -t garmin-activities-download:test .
```

The [Dockerfile](Dockerfile) has four stages. `builder` installs [requirements.txt](requirements.txt) into a virtual environment at `/opt/venv`. `runtime` is the default target and the shipped image. `dev` is the dev container target. Read the comments in the file for what each stage removes and why.

To add a tracker, read the class docstring of `Tracker` in [src/trackers/base.py](src/trackers/base.py) and [CLAUDE.md](CLAUDE.md).

### Releases

Make a release with a version tag:

```bash
git tag v1.0.0
git push --tags
```

The tag push builds, scans, and pushes the image as `sha-<commit>` only. It gives the image no version tag and does not move `latest`. A deployment that tracks `latest` must not pull a release before its notes exist. The job summary prints the exact digest to pull for a test.

The tag push also creates a **draft** GitHub release. Replace the `{{RELEASE HIGHLIGHTS}}` placeholder in the draft, then publish it. Publishing is what applies the version tags, to the digest that was already built and scanned:

| Publish the release as | Image tags applied |
|------------------------|--------------------|
| Pre-release | `X.Y.Z-rc<N>`, where `N` is the first free number. No `latest` |
| Release | `X.Y.Z`, `X.Y`, `latest`, and `X` for a major version above 0 |

Converting a pre-release into a release applies the second row to the same digest. A tag with a suffix, such as `v1.2.3-beta.1`, only gets its exact version.

Every pull request needs exactly one `changelog:` label. The label decides the section that holds the pull request in the notes. A pull request with no label fails its checks.

> [!CAUTION]
> Do not delete the tag of a release that you published. GitHub changes that release back to a draft, but the image keeps the version tags it was given, `latest` included. A tag that already has a published release also fails the build, because its notes are written by hand and must not be rewritten.

## License

Released under the MIT License. See [LICENSE](LICENSE).
