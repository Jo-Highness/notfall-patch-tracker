#!/usr/bin/env bash
# Prueft die gepinnten Abhaengigkeiten (requirements.lock / requirements-dev.lock) mit
# pip-audit gegen bekannte Schwachstellen (PyPI/OSV). Laeuft in einem Wegwerf-Container,
# installiert nichts in das App-Image. Exitcode != 0 bei Befund.
#   scripts/audit-deps.sh
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${UV_IMAGE:-ghcr.io/astral-sh/uv:python3.12-bookworm-slim}"
PIP_AUDIT="${PIP_AUDIT_VERSION:-2.10.1}"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uvc \
  -v "$PWD:/w:ro" -w /w "$IMAGE" sh -c "
    set -e
    for lock in requirements.lock requirements-dev.lock; do
      echo \"== \$lock\"
      uvx --quiet pip-audit==$PIP_AUDIT -r \"\$lock\" --disable-pip --require-hashes --progress-spinner off
    done"
