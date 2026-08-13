# Three stages share one base:
#   builder -- installs requirements.txt into a venv at /opt/venv
#   dev     -- the dev container target, keeps pip so dev tooling installs
#   runtime -- the shipped image, last stage and therefore the default target
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fixed UID/GID 1000. Deployments needing a different user override it at run
# time -- compose `user:`, k8s `securityContext.runAsUser` -- instead of
# rebuilding, since /app/data and /app/tokens are always mount points and the
# ownership set below is shadowed by whatever the host or volume provides.
# Nothing in src/ needs a home directory or a passwd entry, so the process runs
# fine as a UID that does not exist in this image.
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -m -s /bin/bash appuser

WORKDIR /app


FROM base AS builder

COPY requirements.txt .

# pip only exists here to populate the venv; the runtime stage copies the tree
# as-is, so drop pip from it rather than shipping a second copy of pip's
# vendored dependency set.
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt && \
    rm -rf /opt/venv/lib/python3.*/site-packages/pip \
           /opt/venv/lib/python3.*/site-packages/pip-*.dist-info \
           /opt/venv/bin/pip*


# Built by .devcontainer/devcontainer.json via `"target": "dev"`. It keeps pip
# because postCreateCommand installs requirements-dev.txt at create time, and
# it skips the venv so that `pip install --user` and the workspace mount behave
# the way they did before the runtime stage was split out.
FROM base AS dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

USER appuser


FROM base AS runtime

ENV PATH="/opt/venv/bin:$PATH"

# Build-time machinery that `python -m src.main` never touches. pip is the one
# that matters for scanning: it vendors its own dependency set and, since pip
# 26.2, ships pip/_vendor/bom.cdx.json describing it, so any image keeping pip
# reports CVEs for packages this app never imports (msgpack, setuptools).
# ensurepip bundles a whole pip wheel for the same reason. apt and dpkg stay --
# removing them would hide the OS package inventory from image scanners, which
# trades real coverage for a smaller report.
RUN rm -rf /usr/local/lib/python3.*/site-packages/pip \
           /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.*/ensurepip \
           /usr/local/lib/python3.*/idlelib \
           /usr/local/lib/python3.*/turtledemo \
           /usr/local/lib/python3.*/tkinter \
           /usr/local/lib/python3.*/lib-dynload/_tkinter.*.so \
           /usr/local/lib/python3.*/pydoc_data \
           /usr/local/lib/python3.*/config-3.*-*-linux-gnu \
           /usr/local/include/python3.* \
           /usr/local/bin/pip* \
           /usr/local/bin/idle* \
           /usr/local/bin/pydoc* \
           /usr/local/bin/*-config

COPY --from=builder /opt/venv /opt/venv

COPY --chown=appuser:appuser src/ ./src/

RUN mkdir -p /app/data /app/tokens && \
    chown -R appuser:appuser /app/data /app/tokens

USER appuser

CMD ["python", "-m", "src.main"]
