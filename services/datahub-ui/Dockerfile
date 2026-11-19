# syntax=docker/dockerfile:1.7
# Data Hub — Streamlit UI (also runs the Dagster Prometheus exporter with another command).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/datahub

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 datahub \
 && useradd --uid 1000 --gid datahub --create-home --home-dir /home/datahub datahub

WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml streamlit_app.py entrypoint.sh ./
COPY .streamlit ./.streamlit
COPY datahub_ui ./datahub_ui
RUN pip install --no-deps . && chown -R datahub:datahub /app

ARG GIT_SHA=unknown
ARG VERSION=1.0.0
LABEL org.opencontainers.image.title="datahub-ui" \
      org.opencontainers.image.source="https://github.com/plyts/datahub" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV DATAHUB_GIT_SHA=${GIT_SHA} DATAHUB_VERSION=${VERSION}

USER datahub
EXPOSE 8501 9188
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8501/_stcore/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
