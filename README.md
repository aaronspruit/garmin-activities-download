
# Garmin Activities Download

Run-once container that fetches Garmin Connect activities as GPX, FIT, and/or TCX files, deployable via Docker Compose or a Kubernetes CronJob

## Overview

This container connects to Garmin Connect, fetches recent activities, and saves each one as a GPX, FIT, and/or TCX file. It authenticates once using saved tokens, downloads any activities it has not already saved, then exits. That run-once design makes it a natural fit for a Kubernetes CronJob or a host crontab entry, rather than a long-running service.

Authentication tokens persist across runs, so the container only needs interactive credentials (including MFA) during initial setup. Every scheduled run afterward reuses those tokens headlessly, refreshing and re-saving them as they approach expiry.

## Quick Start (Docker Compose)

Copy the example environment file and set your Garmin credentials:

```bash
cp .env.example .env
```

Edit `.env` and set `GARMIN_EMAIL` and `GARMIN_PASSWORD`.

Run interactive setup once to authenticate and handle MFA:

```bash
docker compose run --rm -it garmin-sync python -m src.setup
```

This saves tokens to the `./tokens` volume. After setup, run the sync manually whenever you want:

```bash
docker compose run --rm garmin-sync
```

To automate runs, add an entry to the host crontab that calls `docker compose run --rm garmin-sync` on a schedule:

```cron
0 6 * * * cd /path/to/garmin-activities-download && docker compose run --rm garmin-sync
```

Downloaded activity files land in `./data`, organized into `FIT`, `GPX`, and `TCX` subfolders based on the formats you configure, and tokens persist in `./tokens` between runs. See [Output files](#output-files) for the naming scheme.

### File ownership on Linux

The container runs as UID/GID 1000, so files written to `./data` and `./tokens` are owned by that user. If your host user is different, set `UID` and `GID` in `.env` to your own `id -u` and `id -g` values:

```dotenv
UID=1001
GID=1001
```

[compose.yaml](compose.yaml) applies these at run time, so they take effect on the next run with no rebuild — including against the pre-built image from GHCR. Set them in `.env` rather than inline on the command line, since `bash` treats `UID` as a readonly variable and `UID=1001 docker compose run ...` fails.

## Kubernetes Deployment

The [k8s/cronjob.yaml](k8s/cronjob.yaml) manifest defines a PersistentVolumeClaim for token storage, a Secret for credentials, and a CronJob that runs the sync on a schedule.

Tokens need to exist in the PVC before the CronJob's first scheduled run, since the container runs headlessly and cannot prompt for MFA. Choose one of these approaches:

* Run an interactive pod that mounts the same PVC and execute setup inside it:

  ```bash
  kubectl run garmin-setup --rm -it --image=ghcr.io/OWNER/garmin-activities-download:latest \
    --overrides='{"spec":{"containers":[{"name":"garmin-setup","image":"ghcr.io/OWNER/garmin-activities-download:latest","command":["python","-m","src.setup"],"stdin":true,"tty":true,"volumeMounts":[{"name":"tokens","mountPath":"/app/tokens"}]}],"volumes":[{"name":"tokens","persistentVolumeClaim":{"claimName":"garmin-tokens-pvc"}}]}}'
  ```

* Run setup locally against the `./tokens` directory, then copy the resulting token files into the PVC with `kubectl cp` (via a temporary pod that mounts the PVC).

Once tokens exist in the PVC, apply the manifest:

```bash
kubectl apply -f k8s/cronjob.yaml
```

Update the Secret's `GARMIN_EMAIL` and `GARMIN_PASSWORD` values and the container image reference before applying. The manifest ships with a `hostPath` volume for output data, with a PVC alternative available in the comments for multi-node clusters.

The pod runs as UID/GID 1000 via `securityContext`, with `fsGroup` set so the kubelet chowns the token PVC to a group the non-root process can write to. Adjust `runAsUser`/`runAsGroup` if your storage requires a different owner.

> [!IMPORTANT]
> `fsGroup` does not apply to `hostPath` volumes. `DirectoryOrCreate` creates the output path as `root:root`, which the non-root container cannot write to, so if you use Option A create the directory on the node first:
>
> ```bash
> sudo mkdir -p /data/garmin-gpx && sudo chown 1000:1000 /data/garmin-gpx
> ```
>
> Option B (the PVC) needs no such step.

## Configuration

| Variable | Default | Description |
|----------|---------|--------------|
| `GARMIN_EMAIL` | none | Garmin Connect account email, used for initial authentication and credential fallback |
| `GARMIN_PASSWORD` | none | Garmin Connect account password, used for initial authentication and credential fallback |
| `DAYS_BACK` | `7` | Number of days of activity history to check on each run |
| `GARMINTOKENS` | `/app/tokens` | Path where authentication tokens are read from and written to |
| `OUTPUT_DIR` | `/app/data` | Path where downloaded activity files are saved, in one folder per format (with optional subfolder) |
| `DOWNLOAD_FORMATS` | `FIT` | Comma-separated list of download formats. Each is a format (`FIT`, `GPX`, `TCX`) with an optional `:subfolder` nested under that format's folder |

`GARMIN_EMAIL` and `GARMIN_PASSWORD` also support Docker secrets. Set them at `/run/secrets/garmin_email` and `/run/secrets/garmin_password`, and the container reads from those files before falling back to the environment variables.

`DAYS_BACK` must be an integer and every `DOWNLOAD_FORMATS` entry must name one of `FIT`, `GPX`, and `TCX`. Both are validated at startup, and an invalid value fails the run with exit code `2` before any download is attempted.

### Download formats

A downloaded format is a format plus, optionally, a subfolder inside that format's folder. Every path starts with the format, so `data/<FORMAT>/` always holds only that format's files. Written bare, a format saves directly into its own folder, so `DOWNLOAD_FORMATS=FIT,GPX` fills `data/FIT` and `data/GPX`. Add `:subfolder` to nest one level deeper:

```bash
DOWNLOAD_FORMATS=FIT:folderA
```

That writes to `data/FIT/folderA` — never to `data/folderA`.

The same format may appear more than once to fill several folders, bare and subfoldered entries included. This is the way to feed several downstream systems that each delete the files they import — every folder is deduplicated independently, so a system that empties its folder receives the activity again on the next run, while the other folders are left alone:

```bash
DOWNLOAD_FORMATS=FIT,FIT:strava-inbox,FIT:archive,GPX
```

That fills `data/FIT`, `data/FIT/strava-inbox`, `data/FIT/archive`, and `data/GPX`. Each activity is still fetched from Garmin only once per format, no matter how many folders it is written to.

Subfolder names must be a single folder name: no `/` or `\`, and neither `.` nor `..`. Nesting is exactly one level below the format folder. Format names are case-insensitive and subfolder names are used exactly as written. Repeating the same format and subfolder pair is harmless — the duplicate is ignored and the folder is filled once. Two formats may reuse the same subfolder name (`GPX:inbox,TCX:inbox`) without interfering, since each lives under its own format folder.

## Output files

Each activity is saved as `<activityId>.<extension>` inside its format's folder, using the numeric Garmin Connect activity ID rather than the activity's date or name. A format's folder is `<OUTPUT_DIR>/<FORMAT>` for a bare format, or `<OUTPUT_DIR>/<FORMAT>/<subfolder>` when a subfolder is given, so a format's files never leave its own folder. With `DOWNLOAD_FORMATS=FIT,GPX,TCX`:

```
data/
├── FIT/17284419021.fit
├── GPX/17284419021.gpx
└── TCX/17284419021.tcx
```

With subfolders — `DOWNLOAD_FORMATS=FIT,FIT:strava-inbox,FIT:archive,GPX:you@example.com`:

```
data/
├── FIT
│   ├── 17284419021.fit
│   ├── archive/17284419021.fit
│   └── strava-inbox/17284419021.fit
└── GPX
    └── you@example.com/17284419021.gpx
```

### Unsafe activity IDs

The activity ID that names each file comes straight from the Garmin Connect API response, so it is validated before it is used to build a path: it must be made up only of ASCII letters and digits. Garmin returns plain numbers today, and letters are accepted so a future ID scheme keeps working without a code change, but anything else — a path separator, a `..` segment, an absolute path — is rejected.

A rejected ID aborts the run with exit code `3` and logs the offending value; no files are written, including for activities later in the same batch. This is not something a normal run can hit. Seeing it means the response did not come from Garmin unmodified, so treat it as a signal to check what sits between the container and `connect.garmin.com` — an intercepting proxy, a DNS or TLS problem, or a tampered-with `garminconnect` install — rather than as a bug to work around.

### Duplicate downloads
Deduplication is purely filesystem-based: on each run, an activity is skipped for a format when the file already exists in that format's folder. There is no database or manifest, so renaming, moving, or deleting a file causes the next run to download it again.

The downloader waits one second between downloads to stay clear of Garmin's rate limits. This is not configurable, so the first run of a wide `DAYS_BACK` window across several formats takes a while — expect roughly one second per file. Progress is logged per activity.

## Exit codes

The container runs once and exits. Since it is meant to run unattended on a schedule, use the exit code to decide whether a run needs attention:

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success. New activities downloaded, or nothing new to download | None |
| `1` | Authentication failed, and no usable credential fallback was available | Re-run interactive setup (see below) |
| `2` | Any other failure, such as invalid configuration or a Garmin API error | Check the logs; often transient and resolved by the next scheduled run |
| `3` | Garmin returned an activity ID that is not alphanumeric, so no output path was built from it | Check the logs; see [Unsafe activity IDs](#unsafe-activity-ids) |

Codes `1` and `3` are the ones worth alerting on, because neither resolves on its own.

## Troubleshooting

**Authentication fails on a scheduled run (exit code `1`).** Tokens do not normally need manual attention: `garminconnect` refreshes them on each login when they are close to expiring and writes the refreshed set back to the token store, so ordinary scheduled runs stay authenticated indefinitely. Exit code `1` therefore points at something that broke the token store rather than routine expiry — most often an account password change, a sign-out that revoked the session, a token directory that was not persisted between runs, or a long enough gap between runs that the underlying credential lapsed entirely.

The container does fall back to `GARMIN_EMAIL` and `GARMIN_PASSWORD`, but that fallback cannot answer an MFA challenge headlessly. Re-run the interactive setup to mint a fresh token set:

```bash
docker compose run --rm -it garmin-sync python -m src.setup
```

On Kubernetes, re-run setup using either approach from [Kubernetes Deployment](#kubernetes-deployment) so the refreshed tokens land back in the PVC.

**Activities are downloaded again after a reorganization.** Dedup matches on the exact path `<output_dir>/<FORMAT>[/<subfolder>]/<activityId>.<ext>`, built from the download format. Files that were renamed or moved — or that live in a folder no longer named in `DOWNLOAD_FORMATS` — are no longer recognized. Restore the original layout, or add the old subfolder back as a download format, rather than widening `DAYS_BACK`.

**Older activities are never fetched.** `DAYS_BACK` bounds every run, so activities older than that window are never considered. Run once with a larger value to backfill.

## Development

### Dev Container (recommended)

This repo ships a [Dev Container](https://containers.dev/) at [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json) — open it and skip the manual setup below. With Docker running and the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension installed, open the repo folder in VS Code and run **Dev Containers: Reopen in Container** from the Command Palette (`F1`). The first build takes a few minutes; after that the environment is ready when `postCreateCommand` finishes installing dependencies.

It is built from the project [Dockerfile](Dockerfile), so development happens on the same `python:3.14-slim` base and non-root `appuser` as the shipped image. That base is minimal, so `git`, the GitHub CLI, and a configured shell are added as [Features](https://containers.dev/features), and dev dependencies are installed by `postCreateCommand`.

Because the workspace mounts at `/workspaces/garmin-activities-download` rather than the image's `/app`, `OUTPUT_DIR` and `GARMINTOKENS` are overridden to point at the in-repo `./data` and `./tokens` folders. An interactive `python -m src.setup` or `python -m src.main` inside the container therefore reads and writes those directories directly.

You do not need the `UID`/`GID` setting described under [File ownership on Linux](#file-ownership-on-linux). On Linux, `updateRemoteUserUID` remaps `appuser` to your host user when the container is created, so workspace files you create stay owned by you.

> [!NOTE]
> The dev container has no Docker CLI and no mounted Docker socket, so `docker build` and `docker compose` commands cannot run inside it. Run those from a terminal on your host.

### Manual setup

If you prefer to work outside a container, install runtime and development dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Run the test suite with coverage:

```bash
pytest --cov
```

Lint and format-check the code:

```bash
ruff check .
ruff format --check .
```

Build the container image locally:

```bash
docker build -t garmin-activities-download:test .
```

## CI/CD

The [.github/workflows/ci.yml](.github/workflows/ci.yml) workflow runs on pushes and pull requests against `main`, and on `v*.*.*` tags. It defines five jobs: `lint` and `test` run in parallel, then `build-push` builds the image, then `security-scan` runs a Trivy vulnerability scan (`CRITICAL` and `HIGH`) whose results are uploaded to GitHub code scanning, and finally `release` publishes a GitHub release.

How much of that chain runs depends on the event:

| Event | Jobs that run |
|-------|---------------|
| Pull request | `lint`, `test`, and `build-push` — the image is built but not pushed, so `security-scan` and `release` are skipped |
| Push to `main` | The above, plus the image is pushed to GHCR with an attestation, plus `security-scan` |
| `v*.*.*` tag | All five, ending in a published GitHub release |

To cut a release, tag the commit and push the tag:

```bash
git tag v1.0.0
git push --tags
```

## License

Released under the MIT License. See [LICENSE](LICENSE).
