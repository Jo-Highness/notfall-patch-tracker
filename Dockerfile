# syntax=docker/dockerfile:1.7
# Notfall-Patch-Tracker – Multi-Stage-Build (Auftrag 2/8.2, NPT-02/11).
#   builder : venv mit gepinnten Abhaengigkeiten + App
#   test    : builder + Dev-Werkzeuge + Tests (pytest/ruff/mypy), nie ausgeliefert
#   runtime : schlankes Laufzeit-Image, non-root, ohne Build-Tools/pip, PID 1 = tini

ARG PYTHON_IMAGE=python:3.12.16-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

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
# Ungenutzte Debian-Pakete entfernen (SBOM-Scan, DECISIONS K14): Die Laufzeit fuehrt nur
# Python und tini aus und braucht daneben tzdata, ca-certificates und die von Python bzw.
# den Wheels gelinkten Bibliotheken. Entfernt wird, was weder ausgefuehrt noch gelinkt wird
# (util-linux samt Bibliotheken, PAM/passwd samt SELinux-Verwaltungs- und Audit-Bibliotheken
# nach dem Anlegen des Benutzers, ncurses-Programme, tar, SQLite, apt samt sqv/nettle,
# Archiv-Schluesselbund, seccomp und lz4/xxhash, debconf, perl).
# scripts/purge-runtime-packages.sh bricht ab, wenn ein gelistetes Paket fehlt (umbenannt
# nach neuem Basis-Digest) oder der Reststand von scripts/runtime-packages.txt abweicht;
# NPT_PURGE_PACKAGES_LATE folgt in einem zweiten dpkg-Aufruf (PAM-Skripte brauchen debconf,
# dpkg braucht tar).
# Danach hat das Abbild keinen Paketmanager mehr; wer Pakete nachinstallieren will, setzt
# auf dem Basisabbild auf. scripts/check-runtime-libs.sh prueft, dass alle
# verbleibenden Programme und Module ihre Bibliotheken finden.
ARG NPT_PURGE_PACKAGES="apt libapt-pkg7.0 libstdc++6 libudev1 \
  sqv libnettle8t64 libhogweed6t64 debian-archive-keyring libseccomp2 liblz4-1 libxxhash0 \
  passwd login.defs libpam-modules libpam-modules-bin libpam-runtime libpam0g \
  libsemanage2 libsemanage-common libsepol2 libaudit1 libaudit-common libcap-ng0 libbsd0 \
  util-linux mount login bsdutils libmount1 libblkid1 libsmartcols1 liblastlog2-2 libuuid1 \
  ncurses-bin ncurses-base libncursesw6 libsqlite3-0"
ARG NPT_PURGE_PACKAGES_LATE="adduser debconf perl-base tar"
RUN --mount=type=bind,source=scripts/check-runtime-libs.sh,target=/tmp/check-runtime-libs.sh \
    --mount=type=bind,source=scripts/purge-runtime-packages.sh,target=/tmp/purge-runtime-packages.sh \
    --mount=type=bind,source=scripts/runtime-packages.txt,target=/tmp/runtime-packages.txt \
    apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/bin/pip* \
 && groupadd --system --gid 10001 npt \
 && useradd --system --uid 10001 --gid npt --home-dir /nonexistent --shell /bin/false npt \
 && mkdir -p /data/backups && chown npt:npt /data/backups \
 && NPT_PURGE_PACKAGES="$NPT_PURGE_PACKAGES" NPT_PURGE_PACKAGES_LATE="$NPT_PURGE_PACKAGES_LATE" \
    sh /tmp/purge-runtime-packages.sh /tmp/runtime-packages.txt \
 && rm -rf /var/cache/apt /var/cache/debconf /var/lib/apt /etc/apt /var/log/apt /etc/security \
 && sh /tmp/check-runtime-libs.sh /usr/bin /usr/sbin /usr/lib /usr/local
COPY --from=venv-final /opt/venv /opt/venv
RUN --mount=type=bind,source=scripts/check-runtime-libs.sh,target=/tmp/check-runtime-libs.sh \
    sh /tmp/check-runtime-libs.sh /opt/venv \
 && /opt/venv/bin/python -c "import ssl, zlib, bz2, lzma, ctypes, hashlib, uuid, zoneinfo; \
ssl.create_default_context(); uuid.uuid4(); zoneinfo.ZoneInfo('Europe/Berlin'); \
import npt.run, npt.web.public, npt.web.admin, npt.jobs, npt.migrate, npt.mail, npt.export"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC
USER 10001:10001
WORKDIR /tmp
EXPOSE 8000 8001
# Rollenabhaengig (K14): mit public wie bisher GET :8000/healthz, sonst Admin-Port/Heartbeat.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-m", "npt.probe", "--docker"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["npt-run"]

# OCI-Metadaten (J15) am Ende der Stufe: geaenderte Build-Args (z. B. NPT_REVISION je Commit)
# erzeugen nur eine neue Metadaten-Schicht, die Schichten darueber bleiben im Cache.
#   docker build --build-arg NPT_REVISION="$(git rev-parse HEAD)" \
#                --build-arg NPT_SOURCE_URL=https://github.com/<owner>/notfall-patch-tracker .
ARG NPT_VERSION=0.2.1
ARG NPT_REVISION=""
ARG NPT_SOURCE_URL=""
LABEL org.opencontainers.image.title="notfall-patch-tracker" \
      org.opencontainers.image.description="Notfall-Patch-Tracker: Betroffenheitspruefung bei kritischen Schwachstellen" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${NPT_VERSION}" \
      org.opencontainers.image.revision="${NPT_REVISION}" \
      org.opencontainers.image.source="${NPT_SOURCE_URL}"
