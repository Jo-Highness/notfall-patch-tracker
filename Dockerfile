# syntax=docker/dockerfile:1.7
# Notfall-Patch-Tracker – Multi-Stage-Build (Auftrag 2/8.2, NPT-02/11).
#   builder : venv mit gepinnten Abhaengigkeiten + App
#   test    : builder + Dev-Werkzeuge + Tests (pytest/ruff/mypy), nie ausgeliefert
#   runtime : schlankes Laufzeit-Image, non-root, ohne Build-Tools/pip, PID 1 = tini

ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.lock ./
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r requirements.lock
# LICENSE + THIRD_PARTY_NOTICES.md: license-files in pyproject.toml (landen im .dist-info).
COPY pyproject.toml README.md alembic.ini LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
# Handbuecher (eine Quelle, Auftrag 11.2): landen per force-include als npt/handbuch im Paket.
COPY docs/handbuch ./docs/handbuch
RUN /opt/venv/bin/pip install --no-deps .

FROM builder AS test
COPY requirements-dev.lock ./
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r requirements-dev.lock
COPY tests ./tests
COPY scripts ./scripts
ENV PATH=/opt/venv/bin:$PATH
CMD ["sh", "-c", "ruff check . && ruff format --check . && mypy && pytest -q"]

FROM builder AS venv-final
# pip/setuptools werden zur Laufzeit nicht gebraucht.
RUN /opt/venv/bin/pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true

FROM ${PYTHON_IMAGE} AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/bin/pip* \
 && groupadd --system --gid 10001 npt \
 && useradd --system --uid 10001 --gid npt --home-dir /nonexistent --shell /usr/sbin/nologin npt \
 && mkdir -p /data/backups && chown npt:npt /data/backups
COPY --from=venv-final /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC
USER 10001:10001
WORKDIR /tmp
EXPOSE 8000 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["npt-run"]

# OCI-Metadaten (J15) am Ende der Stufe: geaenderte Build-Args (z. B. NPT_REVISION je Commit)
# erzeugen nur eine neue Metadaten-Schicht, die Schichten darueber bleiben im Cache.
#   docker build --build-arg NPT_REVISION="$(git rev-parse HEAD)" \
#                --build-arg NPT_SOURCE_URL=https://github.com/<owner>/notfall-patch-tracker .
ARG NPT_VERSION=0.1.0
ARG NPT_REVISION=""
ARG NPT_SOURCE_URL=""
LABEL org.opencontainers.image.title="notfall-patch-tracker" \
      org.opencontainers.image.description="Notfall-Patch-Tracker: Betroffenheitspruefung bei kritischen Schwachstellen" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${NPT_VERSION}" \
      org.opencontainers.image.revision="${NPT_REVISION}" \
      org.opencontainers.image.source="${NPT_SOURCE_URL}"
