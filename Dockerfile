FROM python:3.14-slim

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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser src/ ./src/

RUN mkdir -p /app/data /app/tokens && \
    chown -R appuser:appuser /app/data /app/tokens

USER appuser

CMD ["python", "-m", "src.main"]
