
# Garmin Activities Download

This container downloads Garmin Connect activities as FIT, GPX, or TCX files. It runs one time, then it exits. You can deploy it with Docker Compose or with a Kubernetes CronJob.

## Overview

The container connects to Garmin Connect and downloads the recent activities. It saves each activity as a FIT, GPX, or TCX file. It authenticates with saved tokens, downloads the new activities, then exits. This run-once design fits a Kubernetes CronJob or a host crontab entry. The container is not a long-running service.

The authentication tokens stay on disk between runs. The container needs interactive credentials and MFA only for the first setup. Each scheduled run after that uses these tokens with no user input. When the expiry time is near, the container refreshes the tokens and saves them again.

## Quick Start (Docker Compose)

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`. Set `GARMIN_EMAIL` and `GARMIN_PASSWORD`.

3. Run the interactive setup one time to authenticate and to answer the MFA challenge:

   ```bash
   docker compose run --rm -it garmin-sync python -m src.setup
   ```

   This command saves the tokens in the `./tokens` volume.

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

The tokens must exist in the PVC before the first scheduled run. The container runs with no user input and cannot prompt for MFA. Use one of these two procedures:

* Run an interactive pod that mounts the same PVC. Then run the setup in that pod:

  ```bash
  kubectl run garmin-setup --rm -it --image=ghcr.io/OWNER/garmin-activities-download:latest \
    --overrides='{"spec":{"containers":[{"name":"garmin-setup","image":"ghcr.io/OWNER/garmin-activities-download:latest","command":["python","-m","src.setup"],"stdin":true,"tty":true,"volumeMounts":[{"name":"tokens","mountPath":"/app/tokens"}]}],"volumes":[{"name":"tokens","persistentVolumeClaim":{"claimName":"garmin-tokens-pvc"}}]}}'
  ```

* Run the setup locally against the `./tokens` directory. Then copy the token files into the PVC with `kubectl cp`, from a temporary pod that mounts the PVC.

When the tokens are in the PVC, apply the manifest:

```bash
kubectl apply -f k8s/cronjob.yaml
```

Before you apply the manifest, set `GARMIN_EMAIL` and `GARMIN_PASSWORD` in the Secret. Also set the container image reference. The manifest includes a `hostPath` volume for the output data. The comments give a PVC alternative for clusters with more than one node.

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
| `GARMIN_EMAIL` | none | Email of the Garmin Connect account. The container uses it for the first authentication. If the saved tokens fail, the container uses it again |
| `GARMIN_PASSWORD` | none | Password of the Garmin Connect account. The container uses it for the first authentication. If the saved tokens fail, the container uses it again |
| `DAYS_BACK` | `7` | Number of past days of activities that each run downloads |
| `GARMINTOKENS` | `/app/tokens` | Path where the container reads and writes the authentication tokens |
| `OUTPUT_DIR` | `/app/data` | Path where the container saves the activity files, in one folder for each format |
| `DOWNLOAD_FORMATS` | `FIT` | Comma-separated list of formats. Each entry is `FIT`, `GPX`, or `TCX`, with an optional `:subfolder` |

`GARMIN_EMAIL` and `GARMIN_PASSWORD` also accept Docker secrets. Put the values in `/run/secrets/garmin_email` and `/run/secrets/garmin_password`. The container reads these files first, and the environment variables second.

`DAYS_BACK` must be an integer. Each `DOWNLOAD_FORMATS` entry must name `FIT`, `GPX`, or `TCX`. The container validates both variables at startup. An invalid value stops the run with exit code `2`, before the first download.

### Download formats

A download format is a format name and, optionally, a subfolder inside the folder of that format. Every path starts with the format name. Therefore `data/<FORMAT>/` holds only the files of that format.

A bare format name writes directly into its own folder. For example, `DOWNLOAD_FORMATS=FIT,GPX` fills `data/FIT` and `data/GPX`. Add `:subfolder` for one more level:

```bash
DOWNLOAD_FORMATS=FIT:folderA
```

This entry writes to `data/FIT/folderA`. It never writes to `data/folderA`.

You can write the same format more than once to fill more than one folder. Bare entries and subfolder entries are both permitted. Use this method to feed several downstream systems that delete the files after import. The container deduplicates each folder independently. A system that empties its folder gets the activity again on the next run, and the other folders do not change.

```bash
DOWNLOAD_FORMATS=FIT,FIT:strava-inbox,FIT:archive,GPX
```

This example fills `data/FIT`, `data/FIT/strava-inbox`, `data/FIT/archive`, and `data/GPX`. The container downloads each activity from Garmin one time for each format, independent of the number of folders.

Subfolder names obey these rules:

* A subfolder name is one folder name. It cannot contain `/` or `\`, and it cannot be `.` or `..`.
* A subfolder is exactly one level below the format folder.
* Format names are case-insensitive. Subfolder names are used exactly as written.
* A repeated format and subfolder pair is permitted. The container ignores the duplicate and fills the folder one time.
* Two formats can use the same subfolder name (`GPX:inbox,TCX:inbox`). Each subfolder stays inside its own format folder.

## Output files

The container saves each activity as `<activityId>.<extension>` inside the format folder. The name is the numeric Garmin Connect activity ID, not the date or the name of the activity. For a bare format, the format folder is `<OUTPUT_DIR>/<FORMAT>`. For a format with a subfolder, the format folder is `<OUTPUT_DIR>/<FORMAT>/<subfolder>`. The files of a format never leave the folder of that format.

This example uses `DOWNLOAD_FORMATS=FIT,GPX,TCX`:

```
data/
├── FIT/17284419021.fit
├── GPX/17284419021.gpx
└── TCX/17284419021.tcx
```

This example uses subfolders, with `DOWNLOAD_FORMATS=FIT,FIT:strava-inbox,FIT:archive,GPX:you@example.com`:

```
data/
├── FIT
│   ├── 17284419021.fit
│   ├── archive/17284419021.fit
│   └── strava-inbox/17284419021.fit
└── GPX
    └── you@example.com/17284419021.gpx
```

Deduplication uses only the filesystem. On each run, if the file is already in the folder of that format, the container skips that activity. There is no database and no manifest. If you rename, move, or delete a file, the next run downloads it again.

The downloader waits one second between downloads to stay below the Garmin rate limits. This delay is not configurable. If `DAYS_BACK` is large and there are several formats, the first run is slow. Expect approximately one second for each file. The container logs the progress for each activity.

### Unsafe activity IDs

The activity ID that names each file comes directly from the Garmin Connect API response. The container validates the ID before it builds a path from it: the ID must contain only ASCII letters and digits. Garmin returns plain numbers today. The container accepts letters, so a future ID scheme continues to work with no code change. The container rejects all other values, such as a path separator, a `..` segment, or an absolute path.

An invalid ID stops the run with exit code `3` and logs the bad value. The container writes no files, also not for the later activities in the same batch. A normal run cannot cause this error. The error shows that the response is not the unmodified Garmin response.

If this error occurs, examine what is between the container and `connect.garmin.com`. The usual causes are an intercepting proxy, a DNS or TLS error, or a modified `garminconnect` installation.

## Exit codes

The container runs one time and exits. It runs on a schedule with no operator, so use the exit code to decide whether a run needs attention.

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success. The container downloaded the new activities, or there was nothing new | None |
| `1` | Authentication failed, and the credentials did not work | Run the interactive setup again (see below) |
| `2` | Other error, for example an invalid configuration or a Garmin API error | Read the logs. This error is often temporary and the next scheduled run corrects it |
| `3` | Garmin returned an activity ID that is not alphanumeric, so the container built no output path | Read the logs. See [Unsafe activity IDs](#unsafe-activity-ids) |

Codes `1` and `3` need an alert, because they do not correct themselves.

## Troubleshooting

**Authentication fails on a scheduled run (exit code `1`).** Tokens do not usually need manual work. When the expiry time is near, `garminconnect` refreshes them at each login. It then writes the new tokens to the token store. Therefore scheduled runs stay authenticated. Exit code `1` shows that something broke the token store. The usual causes are:

* A change of the account password.
* A sign-out that revoked the session.
* A token directory that did not persist between runs.
* A long interval between runs, so the credential expired.

The container then uses `GARMIN_EMAIL` and `GARMIN_PASSWORD`. But these credentials cannot answer an MFA challenge with no user input. Run the interactive setup again to get a new token set:

```bash
docker compose run --rm -it garmin-sync python -m src.setup
```

On Kubernetes, run the setup again with one of the procedures in [Kubernetes Deployment](#kubernetes-deployment). The new tokens then go into the PVC.

**The container downloads activities again after a reorganization.** Deduplication matches the exact path `<output_dir>/<FORMAT>[/<subfolder>]/<activityId>.<ext>`. The container does not recognize files that you renamed or moved. It also does not recognize files in a folder that `DOWNLOAD_FORMATS` no longer names. Restore the initial layout, or add the old subfolder to `DOWNLOAD_FORMATS`. Do not increase `DAYS_BACK`.

**The container never downloads the older activities.** `DAYS_BACK` limits every run, so the container ignores all activities that are older than this window. To backfill, run the container one time with a larger value.

## Development

### Dev Container (recommended)

This repository includes a [Dev Container](https://containers.dev/) at [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json). Use it instead of the manual setup below.

1. Start Docker and install the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.
2. Open the repository folder in VS Code.
3. Run **Dev Containers: Reopen in Container** from the Command Palette (`F1`).

The first build takes some minutes. The environment is ready when `postCreateCommand` completes the installation of the dependencies.

The dev container is built from the project [Dockerfile](Dockerfile), from its `dev` stage. Development therefore uses the same `python:3.14-slim` base image and the same non-root `appuser` as the released image. The `dev` stage keeps `pip`, which the released `runtime` stage removes, so `postCreateCommand` can install the development dependencies. This base image is minimal, so [Features](https://containers.dev/features) add `git`, the GitHub CLI, and a configured shell.

The workspace mounts at `/workspaces/garmin-activities-download`, not at `/app` as in the image. Therefore `OUTPUT_DIR` and `GARMINTOKENS` point to the `./data` and `./tokens` folders in the repository. An interactive `python -m src.setup` or `python -m src.main` in the container reads and writes these directories.

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
