#!/usr/bin/env bash
# Prueft die Lizenzen aller Laufzeit-Abhaengigkeiten (requirements.lock) auf Vertraeglichkeit
# mit der MIT-Lizenz des Projekts. Laeuft in einem Wegwerf-Container mit demselben
# Python-Basisabbild wie das App-Image: die gelockten Pakete werden wie im Dockerfile
# (--require-hashes --no-deps) in ein eigenes venv installiert, pip-licenses liegt in einem
# zweiten venv und liest das erste ueber --python aus (Werkzeug taucht nicht im Befund auf).
#
#   scripts/check-licenses.sh            Tabelle + Bewertung, Exitcode != 0 bei Befund
#   JSON_OUT=lizenzen.json scripts/check-licenses.sh   zusaetzlich Rohdaten (pip-licenses JSON)
#
# Befund (Exitcode 1): GPL oder AGPL (jede Version) in einer Laufzeit-Abhaengigkeit.
# LGPL ist erlaubt (dynamisch gelinkt/importiert, z. B. psycopg), aber wird ausgewiesen.
# Pakete ohne erkennbare Lizenz (UNKNOWN) sind ebenfalls ein Befund.
# Zusaetzlich werden native Bibliotheken gelistet, die Wheels mitbringen (*.libs-Verzeichnisse,
# z. B. libpq in psycopg-binary) – deren Lizenzen stehen in THIRD_PARTY_NOTICES.md.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${PYTHON_IMAGE:-python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337}"
PIP_LICENSES="${PIP_LICENSES_VERSION:-5.5.5}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

docker run --rm -i --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_NO_CACHE_DIR=1 -e PIP_LICENSES="$PIP_LICENSES" \
  -v "$PWD:/w:ro" -v "$TMP:/out" -w /w "$IMAGE" sh -eu <<'EOF'
python -m venv /tmp/rt
/tmp/rt/bin/pip install --quiet --require-hashes --no-deps -r requirements.lock
python -m venv /tmp/tool
/tmp/tool/bin/pip install --quiet "pip-licenses==$PIP_LICENSES"
/tmp/tool/bin/pip-licenses --python /tmp/rt/bin/python --from=mixed --with-urls \
  --format=json > /out/licenses.json
SITE="$(/tmp/rt/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
python - "$SITE" <<'PY'
import json
import re
import sys
from pathlib import Path

site = Path(sys.argv[1])
rows = json.loads(Path("/out/licenses.json").read_text())
# pip/setuptools stammen aus dem venv selbst, nicht aus requirements.lock.
rows = [r for r in rows if r["Name"].lower() not in {"pip", "setuptools", "wheel"}]
strong = re.compile(r"\bA?GPL|GNU (Affero )?General Public", re.IGNORECASE)
weak = re.compile(r"LGPL|Lesser General Public", re.IGNORECASE)
fail, notes = [], []
print(f"{'Paket':<22} {'Version':<10} Lizenz")
for r in sorted(rows, key=lambda r: r["Name"].lower()):
    lic = r["License"]
    print(f"{r['Name']:<22} {r['Version']:<10} {lic}")
    if not lic or lic.strip().upper() == "UNKNOWN":
        fail.append(f"{r['Name']}: keine Lizenzangabe")
    elif strong.search(weak.sub("", lic)):
        fail.append(f"{r['Name']}: {lic}")
    elif weak.search(lic):
        notes.append(f"{r['Name']}: {lic} (schwaches Copyleft, erlaubt)")
print(f"\n{len(rows)} Laufzeit-Pakete geprueft.")
print("\nGebuendelte native Bibliotheken (Wheels):")
for libs in sorted(site.glob("*.libs")):
    print(f"  {libs.name}: " + ", ".join(sorted(p.name for p in libs.iterdir())))
for n in notes:
    print(f"Hinweis: {n}")
if fail:
    print("\nBEFUND – nicht mit MIT-Weitergabe vereinbar oder ungeklaert:")
    for f in fail:
        print(f"  {f}")
    sys.exit(1)
print("\nOK: keine GPL/AGPL-Lizenz in den Laufzeit-Abhaengigkeiten.")
PY
EOF
if [[ -n "${JSON_OUT:-}" ]]; then cp "$TMP/licenses.json" "$JSON_OUT"; fi
