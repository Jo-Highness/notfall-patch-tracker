#!/usr/bin/env bash
# Erzeugt reproduzierbare SBOMs (CycloneDX 1.6 JSON) nach ./sbom (J15, docs/SBOM.md):
#   <name>-<version>-source.cdx.json    Quellbaum: requirements.lock + vendorte JS-Bibliotheken
#   <name>-<version>-app.cdx.json       App-Abbild     ($APP_IMAGE)
#   <name>-<version>-gateway.cdx.json   Gateway-Abbild ($GATEWAY_IMAGE)
#   <name>-<version>-tls-proxy.cdx.json TLS-Proxy-Abbild ($TLS_PROXY_IMAGE, optionales
#                                       HTTPS-Overlay; fehlt das Abbild, wird es uebersprungen)
# Werkzeuge laufen in Wegwerf-Containern mit gepinnter Version, Abbilder werden per
# `docker save` als Archiv gelesen (kein Docker-Socket im Werkzeug-Container).
#
#   scripts/sbom.sh               alle SBOMs
#   scripts/sbom.sh --scan        zusaetzlich Schwachstellen-Abgleich mit grype (*.grype.txt/.json),
#                                 bewertete Befunde aus dem VEX-Dokument ($VEX) unterdrueckt
#   SKIP_IMAGES=1 scripts/sbom.sh nur Quellbaum (ohne gebaute Abbilder)
#
# Umgebung: APP_IMAGE / GATEWAY_IMAGE / TLS_PROXY_IMAGE (Default
#   notfall-patch-tracker[-gateway|-tls-proxy]:$NPT_IMAGE_TAG, Tag-Default latest), SOURCE_DATE_EPOCH (Default: Zeit des letzten Commits),
#   GRYPE_FAIL_ON (z. B. high: Exitcode != 0 ab diesem Schweregrad), OUT (Default ./sbom),
#   NO_VALIDATE=1 (Schema-Pruefung mit cyclonedx-cli auslassen),
#   VEX (Default vex/notfall-patch-tracker.openvex.json, OpenVEX, K14; NO_VEX=1 = ohne VEX).
set -euo pipefail
cd "$(dirname "$0")/.."

SYFT_IMAGE="${SYFT_IMAGE:-anchore/syft:v1.56.0}"
GRYPE_IMAGE="${GRYPE_IMAGE:-anchore/grype:v0.121.1}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12.16-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30}"
CDX_CLI_IMAGE="${CDX_CLI_IMAGE:-cyclonedx/cyclonedx-cli:0.33.1}"
TAG="${NPT_IMAGE_TAG:-latest}"
APP_IMAGE="${APP_IMAGE:-notfall-patch-tracker:$TAG}"
GATEWAY_IMAGE="${GATEWAY_IMAGE:-notfall-patch-tracker-gateway:$TAG}"
TLS_PROXY_IMAGE="${TLS_PROXY_IMAGE:-notfall-patch-tracker-tls-proxy:$TAG}"
OUT="${OUT:-sbom}"
SCAN=0
[[ "${1:-}" == "--scan" ]] && SCAN=1

NAME="notfall-patch-tracker"
VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n1)"
if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
  if SOURCE_DATE_EPOCH="$(git log -1 --format=%ct 2>/dev/null)"; then :; else
    SOURCE_DATE_EPOCH="$(date +%s)"
    echo "WARNUNG: kein Git und kein SOURCE_DATE_EPOCH – Zeitstempel nicht reproduzierbar" >&2
  fi
fi

mkdir -p "$OUT"
OUT_ABS="$(cd "$OUT" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
U="$(id -u):$(id -g)"

syft() { # quelle ausgabe [syft-optionen...] – Quelle relativ zu $TMP (/in)
  local src="$1" out="$2"; shift 2
  # Das Syft-Abbild hat kein beschreibbares /tmp fuer fremde UIDs -> Arbeitsverzeichnis in /in.
  mkdir -p "$TMP/.work"
  docker run --rm --user "$U" -e HOME=/in/.work -e TMPDIR=/in/.work -e SYFT_CHECK_FOR_APP_UPDATE=false \
    -v "$TMP:/in" "$SYFT_IMAGE" "$src" -q -o "cyclonedx-json@1.6=/in/$out" "$@"
}

finalize() { # roh fertig [optionen...]
  local raw="$1" final="$2"; shift 2
  docker run --rm --network none --user "$U" -v "$PWD:/w:ro" -v "$TMP:/in" -w /w "$PYTHON_IMAGE" \
    python -I scripts/sbom_finalize.py --in "/in/$raw" --out "/in/$final" \
    --epoch "$SOURCE_DATE_EPOCH" --extra scripts/sbom-components.json "$@"
  cp "$TMP/$final" "$OUT/$final"
}

# (a) Quellbaum. Syft erkennt requirements.lock am Dateinamen nicht – Kopie als
# requirements.txt in einem Staging-Verzeichnis; die Fundstelle wird zurueckbenannt.
mkdir -p "$TMP/src"
cp requirements.lock "$TMP/src/requirements.txt"
syft dir:/in/src source.raw.json --source-name "$NAME" --source-version "$VERSION"

# (b)/(c) Abbilder
LIC_ARGS=()
if [[ "${SKIP_IMAGES:-0}" != "1" ]]; then
  for kind in app gateway tls-proxy; do
    case "$kind" in
      app) img="$APP_IMAGE" ;;
      gateway) img="$GATEWAY_IMAGE" ;;
      *) img="$TLS_PROXY_IMAGE" ;;
    esac
    if ! docker image inspect "$img" >/dev/null 2>&1; then
      if [[ "$kind" == tls-proxy ]]; then
        echo "Abbild $img fehlt – TLS-Proxy-SBOM uebersprungen (nur mit HTTPS-Overlay)" >&2
        continue
      fi
      echo "Abbild $img fehlt (bauen oder SKIP_IMAGES=1)" >&2; exit 1
    fi
    docker save "$img" -o "$TMP/$kind.tar"
    syft "docker-archive:/in/$kind.tar" "$kind.raw.json" \
      --source-name "$img" --source-version "$VERSION"
    rm -f "$TMP/$kind.tar"
    finalize "$kind.raw.json" "$NAME-$VERSION-$kind.cdx.json"
  done
  # Lizenzen der Python-Pakete stehen in den installierten Metadaten des App-Abbilds.
  LIC_ARGS=(--licenses-from "/in/$NAME-$VERSION-app.cdx.json")
fi

finalize source.raw.json "$NAME-$VERSION-source.cdx.json" \
  --vendor --root /w --component-type application \
  --app-license MIT --location-map /requirements.txt=/requirements.lock "${LIC_ARGS[@]}"

if [[ "${NO_VALIDATE:-0}" != "1" ]]; then
  for f in "$OUT"/"$NAME-$VERSION"-*.cdx.json; do
    docker run --rm --network none -v "$OUT_ABS:/sbom:ro" "$CDX_CLI_IMAGE" validate \
      --input-file "/sbom/$(basename "$f")" --input-version v1_6 --fail-on-errors >/dev/null \
      || { echo "Schema-Pruefung fehlgeschlagen: $f" >&2; exit 1; }
  done
  echo "Schema CycloneDX 1.6: ok"
fi

if [[ "$SCAN" == 1 ]]; then
  CACHE="${GRYPE_CACHE_DIR:-$HOME/.cache/npt-grype}"
  mkdir -p "$CACHE"
  VEX="${VEX:-vex/notfall-patch-tracker.openvex.json}"
  VEX_ARGS=()
  VEX_MOUNT=()
  if [[ "${NO_VEX:-0}" != "1" ]]; then
    [[ -f "$VEX" ]] || { echo "VEX-Dokument fehlt: $VEX (NO_VEX=1 zum Abschalten)" >&2; exit 1; }
    VEX_MOUNT=(-v "$(cd "$(dirname "$VEX")" && pwd)/$(basename "$VEX"):/vex.json:ro")
    VEX_ARGS=(--vex /vex.json)
  fi
  rc=0
  for f in "$OUT"/"$NAME-$VERSION"-*.cdx.json; do
    base="$(basename "${f%.cdx.json}")"
    # Bericht (Tabelle) zeigt nur offene Befunde; per VEX unterdrueckte stehen im JSON
    # unter ignoredMatches (mit Begruendung im VEX-Dokument).
    docker run --rm --user "$U" -e HOME=/cache -e TMPDIR=/cache -e GRYPE_DB_CACHE_DIR=/cache/db \
      -e GRYPE_CHECK_FOR_APP_UPDATE=false -v "$CACHE:/cache" -v "$OUT_ABS:/sbom" "${VEX_MOUNT[@]}" \
      "$GRYPE_IMAGE" "sbom:/sbom/$(basename "$f")" -q \
      -o "table=/sbom/$base.grype.txt" -o "json=/sbom/$base.grype.json" "${VEX_ARGS[@]}" \
      ${GRYPE_FAIL_ON:+--fail-on "$GRYPE_FAIL_ON"} || rc=1
  done
  # Zusammenfassung je Bericht und VEX-Aussagen ohne Treffer (veraltet, z. B. nach einem
  # Paket-Update: dann erscheint der Befund wieder und ist neu zu bewerten).
  docker run --rm -i --network none --user "$U" -v "$OUT_ABS:/sbom:ro" "${VEX_MOUNT[@]}" \
    "$PYTHON_IMAGE" python -I - "$NAME-$VERSION" <<'PY'
import json, sys
from pathlib import Path
sev = ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"]
used = set()
for f in sorted(Path("/sbom").glob(f"{sys.argv[1]}-*.grype.json")):
    d = json.loads(f.read_text())
    n = {s: 0 for s in sev}
    for m in d.get("matches", []):
        n[m["vulnerability"]["severity"]] = n.get(m["vulnerability"]["severity"], 0) + 1
    ign = d.get("ignoredMatches", []) or []
    for m in ign:
        used.add(m["vulnerability"]["id"])
        for r in m.get("relatedVulnerabilities", []) or []:
            used.add(r["id"])
    counts = " / ".join(f"{n[s]} {s}" for s in sev if n[s] or s in sev[:4])
    print(f"== {f.name.removesuffix('.grype.json')}: offen {counts}; per VEX bewertet {len(ign)}")
vex = Path("/vex.json")
if vex.exists():
    stale = [s["vulnerability"]["name"] for s in json.loads(vex.read_text())["statements"]
             if s["status"] in ("not_affected", "fixed") and s["vulnerability"]["name"] not in used]
    if stale:
        print("VEX-Aussagen ohne Treffer (pruefen/entfernen): " + ", ".join(sorted(set(stale))))
PY
  exit $rc
fi
