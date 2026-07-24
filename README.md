---
title: Garmin Activities Download
description: Run-once container that fetches Garmin Connect activities as GPX files, deployable via Docker Compose or a Kubernetes CronJob
---

## Overview

This container connects to Garmin Connect, fetches recent activities, and saves each one as a GPX file. It authenticates once using saved tokens, downloads any activities it has not already saved, then exits. That run-once design makes it a natural fit for a Kubernetes CronJob or a host crontab entry, rather than a long-running service.

Authentication tokens persist across runs, so the container only needs interactive credentials (including MFA) during initial setup. Every scheduled run afterward reuses those tokens headlessly.

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

Downloaded GPX files land in `./data`, and tokens persist in `./tokens` between runs.

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

## Configuration

| Variable | Default | Description |
|----------|---------|--------------|
| `GARMIN_EMAIL` | none | Garmin Connect account email, used for initial authentication and credential fallback |
| `GARMIN_PASSWORD` | none | Garmin Connect account password, used for initial authentication and credential fallback |
| `DAYS_BACK` | `7` | Number of days of activity history to check on each run |
| `GARMINTOKENS` | `/app/tokens` | Path where authentication tokens are read from and written to |
| `OUTPUT_DIR` | `/app/data` | Path where downloaded GPX files are saved |

`GARMIN_EMAIL` and `GARMIN_PASSWORD` also support Docker secrets. Set them at `/run/secrets/garmin_email` and `/run/secrets/garmin_password`, and the container reads from those files before falling back to the environment variables.

## Development

Install runtime and development dependencies:

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

The [.github/workflows/ci.yml](.github/workflows/ci.yml) workflow runs on every push and pull request against `main`. It chains five jobs: `lint` and `test` run in parallel, `build-push` builds and publishes the image to GHCR once both pass, `security-scan` runs a Trivy vulnerability scan against the published image, and `release` creates a GitHub release when the pipeline runs against a version tag.

To cut a release, tag the commit and push the tag:

```bash
git tag v1.0.0
git push --tags
```
