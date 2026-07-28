FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appuser && \
    useradd -u $UID -g $GID -m -s /bin/bash appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser src/ ./src/

RUN mkdir -p /app/data /app/tokens && \
    chown -R appuser:appuser /app/data /app/tokens

USER appuser

CMD ["python", "-m", "src.main"]
