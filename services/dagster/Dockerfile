# syntax=docker/dockerfile:1.7
# Data Hub — Dagster image (webserver, daemon and code server share it).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home \
    APP_HOME=/opt/dagster/app \
    DBT_PROJECT_DIR=/opt/dagster/app/dbt_project \
    DBT_PROFILES_DIR=/opt/dagster/app/dbt_project

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 dagster \
 && useradd --uid 1000 --gid dagster --create-home --shell /bin/bash dagster

WORKDIR ${APP_HOME}

# Locked dependencies first (layer cache).
COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml dagster.yaml workspace.yaml ./
COPY datahub_pipelines ./datahub_pipelines
COPY dbt_project ./dbt_project
RUN pip install --no-deps . \
 && mkdir -p ${DAGSTER_HOME} /opt/dagster/storage /data/duckdb /data/dbt_artifacts /data/landing \
 && cp dagster.yaml ${DAGSTER_HOME}/dagster.yaml \
 # Pre-compile the dbt manifest so the code location loads without running dbt.
 && cd dbt_project && DUCKDB_PATH=/tmp/parse.duckdb dbt parse --profiles-dir . --target dev \
 && rm -rf logs /tmp/parse.duckdb \
 && chown -R dagster:dagster /opt/dagster /data

ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="datahub-dagster" \
      org.opencontainers.image.source="https://github.com/plyts/datahub" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV DATAHUB_GIT_SHA=${GIT_SHA}

USER dagster
EXPOSE 3000 4000
ENTRYPOINT ["/usr/bin/tini", "--"]
# Default command = code server (overridden for webserver / daemon in docker-compose.yml).
CMD ["dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "datahub_pipelines.definitions", "--location-name", "datahub"]
