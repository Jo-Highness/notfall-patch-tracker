#!/usr/bin/env bash
# JS-Pruefungen ohne lokale Node-Installation (Container):
#   - Syntaxpruefung aller eigenen Skripte unter src/npt/static/js
#   - gemeinsame Filter-Testvektoren (tests/vectors/filter-vectors.json, NPT-01)
# Nutzung: scripts/test-js.sh
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${NODE_IMAGE:-node:22-alpine}"
docker run --rm --network none -v "$PWD:/w:ro" -w /w "$IMAGE" sh -c '
  set -e
  for f in src/npt/static/js/*.js; do node --check "$f"; echo "Syntax ok: $f"; done
  node tests/js/filter-vectors.test.js
'
